"""Toolkit browsing, authoring, moderation, and deletion endpoints."""
import os

from flask import current_app, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, undefer
from werkzeug.utils import secure_filename

from api import api
from api.support import (_access_toolkit, _manage_locked, _serialize_list, persist_toolkit_item as _persist_item,
                         reclaim_asset)
from extensions import db
from models import (Favorite, FileAsset, FileReference, Lesson, LessonProgress, Purchase, Toolkit, ASSET_PATH)
from security import (APIError, assert_identity, current_user, json_object, optional_user,
                      positive_id, request_identity, text)
from access import is_visible
from file_service import (UploadContext, bind_reference, lock_assets, referenced_asset_ids,
                          safe_stored_name, source_asset, unbind_toolkit)
from validation import CATEGORIES, IMAGE_EXTENSIONS, MAX_BATCH_RESOURCES
from services.toolkit import (parse_lessons_json, toolkit_form_errors, validate_toolkit_completeness,
                              _reclaim_lesson_raw)
import money_service as money


def _single_item(partial=False):
    data = json_object() if request.is_json else request.form
    status = data.get("status") if not partial else None
    if status is not None and status not in {"draft", "published"}:
        raise APIError("状态无效")
    allow_incomplete = not partial and status == "draft"
    if partial:
        fields = {"_partial_fields": dict(data), "cover_url": None}
    else:
        error, fields = toolkit_form_errors(data, allow_incomplete=allow_incomplete)
        if error:
            raise APIError(error)
        fields["cover_url"] = fields["cover"]
    error, lessons = parse_lessons_json(data.get("lessons"), allow_incomplete=allow_incomplete)
    if error:
        raise APIError(error)
    if partial and "lessons" not in data:
        lessons = None
    cover_uploads = request.files.getlist("cover_file")
    if len(cover_uploads) > 1:
        raise APIError("每次只能上传一张封面")
    cover_file = cover_uploads[0] if cover_uploads and cover_uploads[0].filename else None
    if cover_file and data.get("cover", "").strip():
        raise APIError("封面图片请选择上传文件或填写 URL，不可同时使用")
    files = [f for f in request.files.getlist("resource") if f and f.filename]
    if len(files) > MAX_BATCH_RESOURCES:
        raise APIError(f"附件不能超过 {MAX_BATCH_RESOURCES} 个")
    streams = [(f.stream, f.filename) for f in files]
    # Extract lesson document files (lesson_file_0, lesson_file_1, etc.)
    lesson_files = []
    for i in range(len(lessons) if lessons else 0):
        if len(request.files.getlist(f"lesson_file_{i}")) > 1 or len(request.files.getlist(f"lesson_image_{i}")) > 1:
            raise APIError("每节教程只能上传一个原件和一张配图")
        file = request.files.get(f"lesson_file_{i}")
        lesson_files.append(file if file and file.filename else None)

    # Add lesson_files to lessons data
    if lessons:
        for i, lesson in enumerate(lessons):
            if i < len(lesson_files) and lesson_files[i]:
                if lesson.get("_clear_document"):
                    raise APIError(f"第 {i + 1} 节不可同时清除与上传教程原件")
                lesson["_file"] = lesson_files[i]
            image = request.files.get(f"lesson_image_{i}")
            if image and image.filename:
                if lesson.get("image_url"):
                    raise APIError(f"第 {i + 1} 节配图不可同时上传文件和填写 URL")
                lesson["image_file"] = {"name": image.filename, "stream": image.stream}
    if any(key.startswith(("lesson_file_", "lesson_image_")) and key.rsplit("_", 1)[-1].isdigit()
           and int(key.rsplit("_", 1)[-1]) >= len(lessons or []) for key in request.files):
        raise APIError("教程附件索引超出范围")
    if not partial and status != "draft":
        validate_toolkit_completeness(fields["title"], fields["description"], [
            (lesson["title"], lesson["duration"], lesson["content"], bool(lesson.get("_file")))
            for lesson in lessons
        ])

    return {**fields, "cover_file": {"name": cover_file.filename, "stream": cover_file.stream} if cover_file else None,
            "resource_files": [], "resource_streams": streams,
            "status": status, "lessons": lessons}


@api.get("/api/toolkits")
def list_toolkits():
    user = optional_user()
    query = Toolkit.query.filter(Toolkit.status == "published")
    keyword = text(request.args.get("q", ""), "关键词", 120)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(or_(Toolkit.title.like(like), Toolkit.summary.like(like), Toolkit.tags.like(like)))
    category = request.args.get("category")
    if category and category != "全部":
        if category not in CATEGORIES:
            raise APIError("分类无效")
        query = query.filter_by(category=category)
    price = request.args.get("price")
    if price not in {None, "all", "free", "paid"}:
        raise APIError("价格筛选无效")
    if price == "free":
        query = query.filter(Toolkit.price == 0)
    elif price == "paid":
        query = query.filter(Toolkit.price > 0)
    sort = request.args.get("sort", "recommended")
    sorts = {"recommended": (Toolkit.featured.desc(), Toolkit.downloads.desc()), "newest": (Toolkit.created_at.desc(),),
             "popular": (Toolkit.downloads.desc(),), "price": (Toolkit.price.asc(),)}
    if sort not in sorts:
        raise APIError("排序方式无效")
    query = query.order_by(*sorts[sort], Toolkit.id)
    try:
        page, per_page = int(request.args.get("page", 1)), int(request.args.get("per_page", 60))
    except ValueError:
        raise APIError("页码无效") from None
    if not 1 <= page <= 100000 or not 1 <= per_page <= 100:
        raise APIError("页码或每页数量无效")
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({"items": _serialize_list(pagination.items, user), "total": pagination.total,
                    "page": page, "pages": pagination.pages})


@api.get("/api/toolkits/<int:toolkit_id>")
def get_toolkit(toolkit_id):
    user = optional_user()
    toolkit, purchased = _access_toolkit(db.session, toolkit_id, user, detail=True)
    return jsonify(toolkit.serialize(user.id if user else None, detail=True, user=user,
                                    purchased_ids={toolkit_id} if purchased else set()))


@api.post("/api/toolkits")
@jwt_required()
def create_toolkit():
    item, identity = _single_item(), request_identity()
    context = UploadContext(identity)
    try:
        result = _persist_item(item, identity, context)
    except Exception:
        context.resolve_failure()
        raise
    return jsonify(result), 201


@api.put("/api/toolkits/<int:toolkit_id>")
@jwt_required()
def edit_toolkit(toolkit_id):
    item, identity = _single_item(partial=True), request_identity()
    context = UploadContext(identity)
    try:
        result = _persist_item(item, identity, context, toolkit_id=toolkit_id)
    except Exception:
        context.resolve_failure()
        raise
    return jsonify(result)


@api.put("/api/toolkits/<int:toolkit_id>/status")
@jwt_required()
def update_status(toolkit_id):
    status = json_object().get("status")
    if not isinstance(status, str) or status not in {"published", "offline", "draft"}:
        raise APIError("状态无效")
    identity = request_identity()
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        toolkit, _, user = _manage_locked(session, toolkit_id, identity)
        if user.role != "admin":
            # Authors publish their own drafts and always take live toolkits
            # offline; re-listing an offline toolkit stays an admin action.
            allowed = ((toolkit.status == "draft" and status in {"published", "draft"})
                       or (toolkit.status in {"published", "draft"} and status == "offline"))
            if not allowed:
                raise APIError("作者只能发布草稿或下架自己的工具包", 403)
        if status == "published":
            lessons = session.execute(select(Lesson).where(Lesson.toolkit_id == toolkit_id)
                                      .options(undefer(Lesson.content))
                                      .order_by(Lesson.sort_order, Lesson.id)).scalars().all()
            raw_lesson_ids = set(session.execute(select(FileReference.lesson_id).where(
                FileReference.toolkit_id == toolkit_id, FileReference.usage == "lesson_raw")).scalars())
            validate_toolkit_completeness(toolkit.title, toolkit.description, [
                (lesson.title, lesson.duration, lesson.content,
                 bool(lesson.raw_file_path or lesson.id in raw_lesson_ids))
                for lesson in lessons
            ])
        toolkit.status = status
        session.flush()
        result = toolkit.serialize(user.id, user=user)
    return jsonify(result)


@api.delete("/api/toolkits/<int:toolkit_id>")
@jwt_required()
def delete_toolkit(toolkit_id):
    identity = request_identity()
    asset_ids = []
    retired_raw = []
    with Session(db.engine) as session, session.begin():
        toolkit, orders, user = _manage_locked(session, toolkit_id, identity)
        purchase = session.execute(select(Purchase.id).where(Purchase.toolkit_id == toolkit_id).with_for_update()).first()
        if orders or purchase:
            toolkit.status = "offline"
            result = {"message": "该工具包存在订单或购买记录，已改为下架", "mode": "offline"}
        else:
            asset_ids = referenced_asset_ids(session, toolkit_id)
            assets = lock_assets(session, asset_ids)
            unbind_toolkit(session, toolkit_id, assets)
            lessons = session.execute(select(Lesson.id, Lesson.raw_file_path).where(
                Lesson.toolkit_id == toolkit_id)).all()
            lesson_ids = [lesson_id for lesson_id, _ in lessons]
            retired_raw = [(lesson_id, raw_path) for lesson_id, raw_path in lessons if raw_path]
            if lesson_ids:
                session.execute(delete(LessonProgress).where(LessonProgress.lesson_id.in_(lesson_ids)))
            session.execute(delete(Favorite).where(Favorite.toolkit_id == toolkit_id))
            session.execute(delete(Lesson).where(Lesson.toolkit_id == toolkit_id))
            session.delete(toolkit)
            result = {"message": "工具包已删除", "mode": "deleted"}
    for lesson_id, raw_path in retired_raw:
        try:
            _reclaim_lesson_raw(lesson_id, raw_path)
        except (OSError, SQLAlchemyError):
            current_app.logger.warning("Deferred deleted lesson original cleanup for lesson %s", lesson_id)
    for asset_id in sorted(set(asset_ids)):
        try:
            reclaim_asset(asset_id)
        except Exception:
            current_app.logger.warning("Committed file cleanup deferred for asset %s", asset_id)
    return jsonify(result)
