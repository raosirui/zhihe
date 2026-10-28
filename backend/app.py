"""Zhihe HTTP API. Importing this module performs no configuration or writes.

Use create_app(config_override=None). Schema/storage initialization and demo
seeding are explicit operator actions, never factory side effects.
"""
import io
import json
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from tempfile import TemporaryDirectory
from functools import wraps
from flask import Flask, Blueprint, current_app, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import create_access_token, jwt_required
from sqlalchemy import select, delete, update, or_
from sqlalchemy.orm import Session, selectinload, undefer, joinedload
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from extensions import db, jwt
from config import build_config
from models import (User, Toolkit, Lesson, Favorite, Order, Purchase, WalletTransaction,
                    WalletReceipt, LessonProgress, RevokedToken, RateLimitGuard, RateLimitBucket,
                    FileAsset, FileReference, FileOperation, utcnow, ASSET_PATH, LESSON_ASSET_PATH)
from security import (APIError, current_user, optional_user, optional_user_id, admin_required,
                      request_identity, assert_identity, configure_jwt, json_object, text,
                      positive_id, rate_hits, rate_hit, client_ip, account_rate_key)
from access import is_manager, is_visible, can_learn, can_download
import money_service as money
from file_service import (UploadContext, directory, init_storage, safe_stored_name, source_asset,
                          lock_assets, bind_reference, unbind_toolkit, referenced_asset_ids,
                          reclaim_asset, recover_file_operations)
from batch_kit import PROMPT_TEXT, build_skill_zip, build_template_zip, load_batch_package, ManifestError
from validation import parse_money, CATEGORIES, LEVELS, IMAGE_EXTENSIONS, string_field, image_source

api = Blueprint("api", __name__)
DEMO_PASSWORDS = {"admin": "Admin@123", "researcher": "User@123", "creator": "Creator@123"}
COMMISSION_RATE = money.COMMISSION_RATE


def create_app(config_override=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(build_config(config_override))
    db.init_app(app)
    jwt.init_app(app)
    configure_jwt()
    origins = app.config["FRONTEND_ORIGIN"]
    CORS(app, origins=origins if isinstance(origins, list) else [origins], supports_credentials=False,
         allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])
    app.register_blueprint(api)

    @app.before_request
    def validate_auth_header():
        header_name = app.config.get("JWT_HEADER_NAME", "Authorization")
        if header_name in request.headers:
            value = request.headers[header_name]
            if len(value) > 8192 or not re.fullmatch(r"Bearer [^\s,]+", value.strip()):
                raise APIError("登录状态无效，请重新登录", 401)

    @app.errorhandler(APIError)
    def api_error(error):
        data = {"message": error.message}
        if error.code:
            data["code"] = error.code
        return jsonify(data), error.status

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return jsonify({"message": str(error)}), 400

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        db.session.rollback()
        app.logger.error("Database request failed (%s)", type(error).__name__)
        return jsonify({"message": "数据服务暂不可用，请稍后重试"}), 503

    @app.errorhandler(OSError)
    def storage_error(error):
        db.session.rollback()
        app.logger.error("Storage request failed (%s)", type(error).__name__)
        return jsonify({"message": "文件服务暂不可用，请稍后重试"}), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {400: "请求格式不正确", 404: "接口或资源不存在", 405: "请求方法不被支持",
                    413: "文件不能超过 100MB", 415: "请求内容类型不支持"}
        return jsonify({"message": messages.get(error.code, "请求失败")}), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        db.session.rollback()
        app.logger.error("Unexpected request failure (%s)", type(error).__name__)
        return jsonify({"message": "服务暂不可用，请稍后重试"}), 500

    @app.after_request
    def harden_responses(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        protected = request.path.startswith((LESSON_ASSET_PATH, "/api/auth/", "/api/wallet", "/api/me/", "/api/orders", "/api/admin/"))
        authenticated = bool(request.headers.get("Authorization"))
        if protected or authenticated or response.status_code >= 400:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        if request.path.startswith((ASSET_PATH, LESSON_ASSET_PATH)):
            response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response
    return app


def init_database():
    """Run only inside an explicitly selected application's context."""
    db.create_all()


def prune_revoked_tokens():
    with Session(db.engine) as session, session.begin():
        session.execute(delete(RevokedToken).where(RevokedToken.expires_at < utcnow()))


def _token(user):
    return create_access_token(identity=str(user.id), additional_claims={"role": user.role, "token_version": user.token_version})


def _purchased(session, user, toolkit_id):
    return bool(user and session.execute(select(Purchase.id).where(Purchase.user_id == user.id,
                                                                    Purchase.toolkit_id == toolkit_id)).first())


def _access_toolkit(session, toolkit_id, user, detail=False):
    stmt = select(Toolkit).where(Toolkit.id == toolkit_id)
    if detail:
        stmt = stmt.options(undefer(Toolkit.description), selectinload(Toolkit.lessons).undefer(Lesson.content))
    toolkit = session.execute(stmt).scalar_one_or_none()
    purchased = _purchased(session, user, toolkit_id) if toolkit else False
    if not toolkit or not is_visible(toolkit, user, purchased):
        raise APIError("工具包不存在", 404)
    return toolkit, purchased


def _serialize_list(items, user):
    ids = [x.id for x in items]
    bought = set(db.session.execute(select(Purchase.toolkit_id).where(Purchase.user_id == user.id,
                 Purchase.toolkit_id.in_(ids))).scalars()) if user and ids else set()
    favorites = set(db.session.execute(select(Favorite.toolkit_id).where(Favorite.user_id == user.id,
                   Favorite.toolkit_id.in_(ids))).scalars()) if user and ids else set()
    return [x.serialize(user.id if user else None, user=user, purchased_ids=bought, favorited_ids=favorites) for x in items]


def toolkit_form_errors(form):
    try:
        title = string_field(form.get("title", ""), "标题", 120, True)
        description = string_field(form.get("description", ""), "介绍", 20000, True)
        summary = string_field(form.get("summary", ""), "摘要", 500) or description[:120]
        category = string_field(form.get("category", "生信分析"), "分类", 50)
        level = string_field(form.get("level", "入门"), "难度", 20)
        if category not in CATEGORIES or level not in LEVELS:
            raise ValueError("分类或难度无效")
        price = parse_money(form.get("price", 0), 0, 9999, "定价")
        tags = string_field(form.get("tags", ""), "标签", 300)
        cover = string_field(form.get("cover", ""), "封面", 500) or None
        return None, {"title": title, "description": description, "summary": summary,
                      "category": category, "level": level, "price": price, "tags": tags, "cover": cover}
    except ValueError as error:
        return str(error), None


def parse_lessons_json(raw):
    try:
        if raw is None or raw == "":
            lessons = []
        elif isinstance(raw, str):
            if len(raw.encode("utf-8")) > 20 * 1024 * 1024:
                raise ValueError("教程正文总量过大")
            lessons = json.loads(raw)
        else:
            lessons = raw
        if not isinstance(lessons, list) or len(lessons) > 50:
            raise ValueError("教程步骤须为不超过 50 项的数组")
        cleaned = []
        total = 0
        for index, lesson in enumerate(lessons):
            if not isinstance(lesson, dict):
                raise ValueError(f"步骤 {index + 1} 格式不正确")
            title = string_field(lesson.get("title", f"步骤 {index + 1}"), "步骤标题", 160, True)
            content = lesson.get("content", "")
            if not isinstance(content, str) or len(content.encode("utf-8")) > 1024 * 1024:
                raise ValueError("步骤正文须为不超过 1MB 的字符串")
            total += len(content.encode("utf-8"))
            if total > 20 * 1024 * 1024:
                raise ValueError("教程正文总量过大")
            image_value = lesson.get("image", "")
            image = string_field("" if image_value is None else image_value, "步骤图片", 500) or None
            duration = lesson.get("duration", 10)
            if type(duration) is not int or not 1 <= duration <= 600:
                raise ValueError("学习时长须为 1～600 的整数")
            preview = lesson.get("preview", lesson.get("isPreview", index == 0))
            if type(preview) is not bool:
                raise ValueError("试看须为 true/false")
            lesson_id = positive_id(lesson["id"], "步骤ID") if "id" in lesson else None
            cleaned.append({"id": lesson_id, "title": title, "content": content, "image_url": image,
                            "image_file": None, "duration": duration, "preview": preview})
        return None, cleaned
    except (ValueError, TypeError) as error:
        return str(error), None


def _canonical_lesson_image(value):
    if value and value.startswith(LESSON_ASSET_PATH):
        stored = value[len(LESSON_ASSET_PATH):]
        if safe_stored_name(stored):
            return "file:" + stored
    return value


def _prepare_item_assets(item, uploads):
    cover = uploads.descriptor("image", item.get("cover_file"))
    resource = uploads.descriptor("resource", item.get("resource_file"))
    if item.get("resource_stream"):
        stream, name = item["resource_stream"]
        # Multipart is already streamed into Werkzeug's private spool; don't
        # materialize it as bytes just to determine its length.
        position = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell() - position
        stream.seek(position)
        resource = uploads.prepare("resource", name, stream, size)
    images = [uploads.descriptor("asset", lesson.get("image_file")) for lesson in (item["lessons"] or [])]
    return cover, resource, images


def _persist_item(item, identity, uploads, toolkit_id=None, skip_existing=False):
    """Single/batch shared import with a fresh transaction per item."""
    if skip_existing:
        # Detect existing titles before any filesystem work/quota reservation.
        # The final check under the same owner lock handles racing new titles.
        with Session(db.engine) as preflight, preflight.begin():
            owner = money.lock_users(preflight, [identity.uid])[identity.uid]
            assert_identity(preflight, owner, identity)
            existing = preflight.execute(select(Toolkit.id).where(Toolkit.author_id == owner.id,
                       Toolkit.title == item["title"]).order_by(Toolkit.id).limit(1)).scalar_one_or_none()
            if existing:
                return {"toolkitId": existing, "status": "skipped"}
    cover, resource, images = _prepare_item_assets(item, uploads)
    allocated = {x["id"] for x in [cover, resource, *images] if x}
    created_id = None
    stale_ids = []
    try:
        with Session(db.engine, expire_on_commit=False) as session, session.begin():
            if toolkit_id:
                toolkit = money.lock_toolkit(session, toolkit_id)
                if not toolkit:
                    raise APIError("工具包不存在", 404)
                money.lock_orders(session, toolkit_id)
                stale_ids = referenced_asset_ids(session, toolkit_id)
            else:
                toolkit = None
            old_cover = toolkit.cover if toolkit else None
            old_lessons = list(session.execute(select(Lesson).where(Lesson.toolkit_id == toolkit_id)
                               .options(undefer(Lesson.content)).order_by(Lesson.sort_order, Lesson.id)
                               .execution_options(populate_existing=True)).scalars()) if toolkit else []
            owner = money.lock_users(session, [identity.uid])[identity.uid]
            assert_identity(session, owner, identity)
            if toolkit and not is_manager(toolkit, owner):
                raise APIError("只能编辑自己发布的工具包", 403)
            if toolkit and "_partial_fields" in item:
                merged = {field: getattr(toolkit, field) for field in
                          ["title", "summary", "description", "category", "level", "price", "tags", "cover"]}
                merged["cover"] = merged["cover"] or ""
                merged.update(item["_partial_fields"])
                if merged.get("cover") is None:
                    merged["cover"] = ""
                error, fields = toolkit_form_errors(merged)
                if error:
                    raise APIError(error)
                item.update(fields)
                item["cover_url"] = fields["cover"]
            if item["lessons"] is None:
                item["lessons"] = [{"id": lesson.id, "title": lesson.title, "content": lesson.content,
                                    "image_url": lesson.image, "image_file": None, "duration": lesson.duration,
                                    "preview": lesson.is_preview} for lesson in old_lessons]
                images = [None] * len(old_lessons)
            if skip_existing:
                # Owner lock is a stable synchronization point for this
                # author's batch duplicate-title check (non-financial path).
                existing = session.execute(select(Toolkit.id).where(Toolkit.author_id == owner.id,
                           Toolkit.title == item["title"]).order_by(Toolkit.id).limit(1)).scalar_one_or_none()
                if existing:
                    return {"toolkitId": existing, "status": "skipped"}
            if not toolkit:
                toolkit = Toolkit(slug=f"{uuid.uuid4().hex[:12]}-{secure_filename(item['title'])[:50] or 'toolkit'}",
                                  author_id=owner.id, status="published", featured=False)
                session.add(toolkit)
            for field in ["title", "summary", "description", "category", "level", "price", "tags"]:
                setattr(toolkit, field, item[field])
            session.flush()
            created_id = toolkit.id
            cover_url = ASSET_PATH + cover["stored_name"] if cover else item.get("cover_url")
            preserve_cover = bool(toolkit_id and not cover and cover_url == old_cover)
            cover_id = cover["id"] if cover else source_asset(session, cover_url, identity, "cover", preserve_unknown=preserve_cover)
            lesson_ids, kept_ids = [], set()
            old_by_id = {lesson.id: lesson for lesson in old_lessons}
            for index, (lesson, prepared) in enumerate(zip(item["lessons"], images)):
                previous = old_by_id.get(lesson["id"]) if lesson.get("id") else (
                    old_lessons[index] if index < len(old_lessons) else None)
                if lesson.get("id") and previous is None:
                    raise APIError("步骤ID不属于此工具包")
                if previous:
                    if previous.id in kept_ids:
                        raise APIError("步骤ID不能重复")
                    kept_ids.add(previous.id)
                url = "file:" + prepared["stored_name"] if prepared else _canonical_lesson_image(lesson.get("image_url"))
                # Preservation is scoped to this exact prior Lesson, never a
                # toolkit-wide set of private images or another toolkit's ID.
                preserved = bool(previous and not prepared and url == _canonical_lesson_image(previous.image))
                asset_id = prepared["id"] if prepared else source_asset(session, url, identity, "lesson", preserve_unknown=preserved)
                lesson_ids.append((url, asset_id, previous, preserved))
            asset_ids = [x for x in [cover_id, resource["id"] if resource else None, *[x[1] for x in lesson_ids], *stale_ids] if x]
            assets = lock_assets(session, asset_ids)
            if toolkit_id:
                unbind_toolkit(session, toolkit.id, {asset_id: assets[asset_id] for asset_id in set(stale_ids)})
                removed_ids = set(old_by_id) - kept_ids
                if removed_ids:
                    session.execute(delete(LessonProgress).where(LessonProgress.lesson_id.in_(removed_ids)))
                    session.execute(delete(Lesson).where(Lesson.id.in_(removed_ids)))
            toolkit.cover = cover_url
            if cover_id:
                bind_reference(session, assets[cover_id], identity, toolkit.id, "cover", preserve=preserve_cover)
            if resource:
                toolkit.resource_name, toolkit.resource_path = resource["name"], resource["stored_name"]
                bind_reference(session, assets[resource["id"]], identity, toolkit.id, "resource")
            elif toolkit_id:
                # Existing resource is unchanged when edit has no replacement.
                # Restore its registry binding rather than lose ownership.
                if toolkit.resource_path:
                    retained = next((x for x in assets.values() if x.stored_name == toolkit.resource_path), None)
                    if retained:
                        bind_reference(session, retained, identity, toolkit.id, "resource", preserve=True)
            for index, (data, (url, asset_id, previous, preserved_image)) in enumerate(zip(item["lessons"], lesson_ids)):
                # Platform images always use the protected policy route. Public
                # shared images keep their underlying image-directory purpose.
                if asset_id:
                    url = "file:" + assets[asset_id].stored_name
                lesson = previous or Lesson(toolkit_id=toolkit.id)
                lesson.title, lesson.content, lesson.image = data["title"], data["content"], url
                lesson.duration, lesson.sort_order, lesson.is_preview = data["duration"], index, data["preview"]
                session.add(lesson)
                session.flush()
                if asset_id:
                    bind_reference(session, assets[asset_id], identity, toolkit.id, "lesson", lesson.id, preserve=preserved_image)
            session.flush()
        # Query with the normal request session only after independent commit.
        db.session.rollback()  # Discard the JWT request's earlier RR snapshot.
        toolkit = db.session.execute(select(Toolkit).where(Toolkit.id == created_id).options(
                  undefer(Toolkit.description), selectinload(Toolkit.lessons).undefer(Lesson.content))).scalar_one()
        result = toolkit.serialize(identity.uid, detail=True)
    except Exception:
        # An acknowledgement failure can happen after COMMIT. Check using a
        # NEW connection, not the rolled-back/stale request snapshot.
        confirmed = False
        if created_id and not toolkit_id:
            try:
                with Session(db.engine) as check:
                    confirmed = check.execute(select(Toolkit.id).where(Toolkit.id == created_id,
                                Toolkit.author_id == identity.uid)).scalar_one_or_none() is not None
            except SQLAlchemyError:
                pass
        if confirmed:
            db.session.rollback()
            toolkit = db.session.get(Toolkit, created_id)
            result = toolkit.serialize(identity.uid, detail=True)
        else:
            uploads.resolve_failure(allocated)
            raise
    for asset_id in sorted(set(stale_ids)):
        try:
            reclaim_asset(asset_id)
        except Exception:
            current_app.logger.warning("Committed old-file cleanup deferred for asset %s", asset_id)
    return result


@api.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "zhihe-api"})


@api.get("/api/stats")
def public_stats():
    return jsonify({"toolkits": Toolkit.query.filter_by(status="published").count(), "users": User.query.count()})


@api.post("/api/auth/login")
def login():
    data = json_object()
    username = text(data.get("username", ""), "账号", 50, 1)
    password = text(data.get("password", ""), "密码", 128, 1, strip=False)
    ip = client_ip()
    try:
        account = account_rate_key(username)
    except SQLAlchemyError:
        raise APIError("认证服务暂不可用", 503)
    rate_hits([(f"login:ip:{ip}", 30, 60), (f"login:account:{account}", 8, 60),
               (f"login:ip:{ip}", 100, 600), (f"login:account:{account}", 30, 600)])
    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        raise APIError("账号或密码不正确", 401)
    return jsonify({"token": _token(user), "user": user.public()})


@api.post("/api/auth/register")
def register():
    data = json_object()
    username = text(data.get("username", ""), "账号", 50, 3)
    password = text(data.get("password", ""), "密码", 128, 8, strip=False)
    nickname = text(data.get("nickname", username), "昵称", 80, 1)
    if not re.fullmatch(r"[\w.\-·]+", username):
        raise APIError("账号需为 3～50 位字母、数字或 _.- 字符")
    ip = client_ip()
    rate_hits([(f"register:{ip}", 5, 60), (f"register:{ip}", 20, 600)])
    try:
        with Session(db.engine, expire_on_commit=False) as session, session.begin():
            if session.execute(select(User.id).where(User.username == username)).first():
                raise APIError("账号已存在", 409)
            user = User(username=username, nickname=nickname, password_hash=generate_password_hash(password), role="user")
            session.add(user)
            session.flush()
            result, token = user.public(), _token(user)
    except IntegrityError:
        raise APIError("账号已存在", 409) from None
    return jsonify({"token": token, "user": result}), 201


@api.get("/api/auth/me")
@jwt_required()
def me():
    return jsonify(current_user().public())


@api.post("/api/auth/logout")
@jwt_required()
def logout():
    identity = request_identity()
    with Session(db.engine) as session, session.begin():
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        if session.get(RevokedToken, identity.jti) is None:
            session.add(RevokedToken(jti=identity.jti, user_id=user.id, expires_at=identity.expires_at))
    return jsonify({"message": "已退出登录"})


@api.put("/api/auth/password")
@jwt_required()
def change_password():
    data = json_object()
    old = text(data.get("oldPassword", ""), "原密码", 128, 1, strip=False)
    new = text(data.get("newPassword", ""), "新密码", 128, 8, strip=False)
    identity = request_identity()
    with Session(db.engine) as session, session.begin():
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        if not check_password_hash(user.password_hash, old):
            raise APIError("原密码不正确")
        user.password_hash = generate_password_hash(new)
        user.token_version += 1
        session.add(RevokedToken(jti=identity.jti, user_id=user.id, expires_at=identity.expires_at))
    return jsonify({"message": "密码已修改，请重新登录"})


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


def _single_item(partial=False):
    data = json_object() if request.is_json else request.form
    if partial:
        fields = {"_partial_fields": dict(data), "cover_url": None}
    else:
        error, fields = toolkit_form_errors(data)
        if error:
            raise APIError(error)
        fields["cover_url"] = fields["cover"]
    error, lessons = parse_lessons_json(data.get("lessons"))
    if error:
        raise APIError(error)
    if partial and "lessons" not in data:
        lessons = None
    resource = request.files.get("resource")
    return {**fields, "cover_file": None, "resource_file": None,
            "resource_stream": (resource.stream, resource.filename) if resource and resource.filename else None,
            "lessons": lessons}


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


def _manage_locked(session, toolkit_id, identity):
    toolkit = money.lock_toolkit(session, toolkit_id)
    if not toolkit:
        raise APIError("工具包不存在", 404)
    orders = money.lock_orders(session, toolkit_id)
    user = money.lock_users(session, [identity.uid])[identity.uid]
    assert_identity(session, user, identity)
    if not is_manager(toolkit, user):
        raise APIError("只能管理自己发布的工具包", 403)
    return toolkit, orders, user


@api.put("/api/toolkits/<int:toolkit_id>/status")
@jwt_required()
def update_status(toolkit_id):
    status = json_object().get("status")
    if not isinstance(status, str) or status not in {"published", "offline"}:
        raise APIError("状态无效")
    identity = request_identity()
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        toolkit, _, user = _manage_locked(session, toolkit_id, identity)
        if user.role != "admin" and status != "offline":
            raise APIError("作者只能下架自己的工具包", 403)
        toolkit.status = status
        session.flush()
        result = toolkit.serialize(user.id, user=user)
    return jsonify(result)


@api.delete("/api/toolkits/<int:toolkit_id>")
@jwt_required()
def delete_toolkit(toolkit_id):
    identity = request_identity()
    asset_ids = []
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
            lesson_ids = list(session.execute(select(Lesson.id).where(Lesson.toolkit_id == toolkit_id)).scalars())
            if lesson_ids:
                session.execute(delete(LessonProgress).where(LessonProgress.lesson_id.in_(lesson_ids)))
            session.execute(delete(Favorite).where(Favorite.toolkit_id == toolkit_id))
            session.execute(delete(Lesson).where(Lesson.toolkit_id == toolkit_id))
            session.delete(toolkit)
            result = {"message": "工具包已删除", "mode": "deleted"}
    for asset_id in sorted(set(asset_ids)):
        try:
            reclaim_asset(asset_id)
        except Exception:
            current_app.logger.warning("Committed file cleanup deferred for asset %s", asset_id)
    return jsonify(result)


@api.get("/api/uploads/<stored>")
def uploaded_cover(stored):
    optional_user()  # Invalid optional credentials still fail, even on public URLs.
    if not safe_stored_name(stored) or os.path.splitext(stored)[1].lower() not in IMAGE_EXTENSIONS:
        raise APIError("资源不存在", 404)
    asset = db.session.execute(select(FileAsset).where(FileAsset.stored_name == stored)).scalar_one_or_none()
    if asset:
        if asset.purpose != "image" or not asset.is_public or asset.state not in {"active", "legacy", "retained"}:
            raise APIError("资源不存在", 404)
        public_ref = db.session.execute(select(FileReference.id).join(Toolkit, FileReference.toolkit_id == Toolkit.id)
                     .where(FileReference.asset_id == asset.id, FileReference.usage == "cover", Toolkit.status == "published")).first()
        legacy_ref = db.session.execute(select(Toolkit.id).where(Toolkit.cover == ASSET_PATH + stored, Toolkit.status == "published")).first()
        if not public_ref and not legacy_ref:
            raise APIError("资源不存在", 404)
    else:
        # No registry means no reliable public/private purpose classification.
        # Explicit migration can register historical public covers safely.
        raise APIError("资源不存在", 404)
    return send_from_directory(directory("image"), stored)


@api.get("/api/lesson-asset/<stored>")
def lesson_asset(stored):
    user = optional_user()
    if not safe_stored_name(stored) or os.path.splitext(stored)[1].lower() not in IMAGE_EXTENSIONS:
        raise APIError("资源不存在", 404)
    lessons = db.session.execute(select(Lesson).where(Lesson.image == "file:" + stored).order_by(Lesson.id)).scalars()
    allowed, visible = False, False
    for lesson in lessons:
        toolkit = lesson.toolkit
        purchased = _purchased(db.session, user, toolkit.id)
        if is_visible(toolkit, user, purchased):
            visible = True
            if can_learn(toolkit, user, purchased, lesson):
                allowed = True
                break
    if not allowed:
        if not visible:
            raise APIError("资源不存在", 404)
        raise APIError("购买后方可查看", 403)
    asset = db.session.execute(select(FileAsset).where(FileAsset.stored_name == stored)).scalar_one_or_none()
    kind = asset.purpose if asset else "asset"
    if kind not in {"asset", "image"} or (asset and asset.state not in {"active", "legacy", "retained"}):
        raise APIError("资源不存在", 404)
    return send_from_directory(directory(kind), stored)


@api.get("/api/batch/template")
def batch_template():
    return send_file(build_template_zip(), mimetype="application/zip", as_attachment=True, download_name="zhihe-standard-template.zip")


@api.get("/api/batch/skill")
def batch_skill():
    return send_file(build_skill_zip(), mimetype="application/zip", as_attachment=True, download_name="zhihe-toolkit-converter-skill.zip")


@api.get("/api/batch/prompt")
def batch_prompt():
    return jsonify({"text": PROMPT_TEXT})


@api.post("/api/batch/upload")
@jwt_required()
def batch_upload():
    identity = request_identity()
    rate_hits([(f"batch:user:{identity.uid}", 10, 600), (f"batch:ip:{client_ip()}", 20, 600)])
    file = request.files.get("package")
    if not file:
        raise APIError("请选择要上传的 ZIP 文件")
    results = []
    uploads = UploadContext(identity)
    # Do not file.read()/BytesIO the ZIP. Its private upload stream and all
    # extracted descriptors stay alive throughout every independent import.
    with TemporaryDirectory(prefix="zhihe-batch-") as staging:
        try:
            with zipfile.ZipFile(file.stream) as zf:
                items, errors = load_batch_package(zf, staging)
        except (zipfile.BadZipFile, ValueError, RuntimeError):
            raise APIError("ZIP 包无效、损坏或清单不符合规范") from None
        except OSError:
            raise APIError("批量暂存服务暂不可用，请稍后重试", 503) from None
        results.extend(errors)
        for item in items:
            try:
                result = _persist_item(item, identity, uploads, skip_existing=True)
                if result.get("status") == "skipped":
                    uploads.resolve_failure()
                    results.append({"index": item["index"], "title": item["title"], "status": "skipped",
                                    "toolkitId": result["toolkitId"], "message": "已有同名工具包，已跳过", "error": "已有同名工具包，已跳过"})
                else:
                    results.append({"index": item["index"], "title": item["title"], "status": "created",
                                    "toolkitId": result["id"], "lessonCount": len(item["lessons"])})
            except (APIError, ValueError) as error:
                uploads.resolve_failure()
                message = error.message if isinstance(error, APIError) else str(error)
                results.append({"index": item["index"], "title": item["title"], "status": "failed",
                                "message": message, "error": message})
            except (OSError, SQLAlchemyError):
                uploads.resolve_failure()
                results.append({"index": item["index"], "title": item["title"], "status": "failed",
                                "message": "条目创建失败，请稍后重试文件或数据服务", "error": "条目创建失败，请稍后重试文件或数据服务"})
    results.sort(key=lambda x: x["index"])
    return jsonify({"total": len(results), "created": sum(x["status"] == "created" for x in results),
                    "failed": sum(x["status"] == "failed" for x in results),
                    "skipped": sum(x["status"] == "skipped" for x in results), "results": results})


@api.post("/api/toolkits/<int:toolkit_id>/favorite")
@jwt_required()
def toggle_favorite(toolkit_id):
    identity = request_identity()
    with Session(db.engine) as session, session.begin():
        toolkit = money.lock_toolkit(session, toolkit_id)
        if not toolkit:
            raise APIError("工具包不存在", 404)
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        if not is_visible(toolkit, user, _purchased(session, user, toolkit_id)):
            raise APIError("工具包不存在", 404)
        favorite = session.execute(select(Favorite).where(Favorite.user_id == user.id, Favorite.toolkit_id == toolkit_id)
                                   .with_for_update()).scalar_one_or_none()
        if favorite:
            session.delete(favorite)
            active = False
        else:
            session.add(Favorite(user_id=user.id, toolkit_id=toolkit_id))
            active = True
    return jsonify({"favorited": active})


@api.get("/api/me/favorites")
@jwt_required()
def favorites():
    user = current_user()
    items = db.session.execute(select(Toolkit).join(Favorite).where(Favorite.user_id == user.id)
                               .order_by(Favorite.created_at.desc())).scalars().all()
    bought = set(db.session.execute(select(Purchase.toolkit_id).where(Purchase.user_id == user.id)).scalars())
    return jsonify(_serialize_list([x for x in items if is_visible(x, user, x.id in bought)], user))


@api.get("/api/me/toolkits")
@jwt_required()
def my_toolkits():
    user = current_user()
    return jsonify(_serialize_list(Toolkit.query.filter_by(author_id=user.id).order_by(Toolkit.created_at.desc()).all(), user))


@api.post("/api/lessons/<int:lesson_id>/complete")
@jwt_required()
def complete_lesson(lesson_id):
    identity = request_identity()
    with Session(db.engine) as session, session.begin():
        toolkit_id = session.execute(select(Lesson.toolkit_id).where(Lesson.id == lesson_id)).scalar_one_or_none()
        if toolkit_id is None:
            raise APIError("步骤不存在", 404)
        toolkit = money.lock_toolkit(session, toolkit_id)
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        lesson = session.execute(select(Lesson).where(Lesson.id == lesson_id).with_for_update()
                                 .execution_options(populate_existing=True)).scalar_one_or_none()
        if not lesson or not toolkit or lesson.toolkit_id != toolkit_id:
            raise APIError("步骤不存在", 404)
        purchase = money.current_purchase(session, user.id, toolkit_id) is not None
        if not is_visible(toolkit, user, purchase):
            raise APIError("步骤不存在", 404)
        if not can_learn(toolkit, user, purchase, lesson):
            raise APIError("购买后方可完成此步骤", 403)
        dialect = session.get_bind().dialect.name
        values = {"user_id": user.id, "lesson_id": lesson_id, "created_at": utcnow()}
        if dialect == "mysql":
            from sqlalchemy.dialects.mysql import insert
            statement = insert(LessonProgress).values(**values)
            session.execute(statement.on_duplicate_key_update(user_id=statement.inserted.user_id))
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
            session.execute(insert(LessonProgress).values(**values).on_conflict_do_nothing(index_elements=["user_id", "lesson_id"]))
        else:
            raise APIError("数据服务不支持当前数据库", 503)
    return jsonify({"completed": True})


@api.get("/api/me/lesson-progress")
@jwt_required()
def lesson_progress():
    user = current_user()
    query = select(LessonProgress.lesson_id).where(LessonProgress.user_id == user.id)
    value = request.args.get("toolkit_id")
    if value is not None:
        try:
            toolkit_id = positive_id(int(value), "工具包ID")
        except ValueError:
            raise APIError("工具包ID无效") from None
        _access_toolkit(db.session, toolkit_id, user)
        query = query.join(Lesson).where(Lesson.toolkit_id == toolkit_id)
    return jsonify(list(db.session.execute(query).scalars()))


@api.post("/api/orders")
@jwt_required()
def create_order():
    result, status = money.create_order(request_identity(), positive_id(json_object().get("toolkitId"), "工具包ID"))
    return jsonify(result), status


@api.post("/api/orders/<int:order_id>/pay")
@jwt_required()
def pay_order(order_id):
    result, status = money.pay_order(request_identity(), order_id)
    return jsonify(result), status


@api.post("/api/orders/<int:order_id>/cancel")
@jwt_required()
def cancel_order(order_id):
    return jsonify(money.cancel_order(request_identity(), order_id))


@api.get("/api/me/orders")
@jwt_required()
def my_orders():
    rows = Order.query.options(joinedload(Order.toolkit), joinedload(Order.buyer), joinedload(Order.seller)).filter_by(
        buyer_id=current_user().id).order_by(Order.created_at.desc()).all()
    return jsonify([x.serialize() for x in rows])


@api.get("/api/wallet")
@jwt_required()
def wallet():
    user = current_user()
    txs = WalletTransaction.query.filter_by(user_id=user.id).order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc()).limit(100).all()
    income = db.session.execute(select(db.func.coalesce(db.func.sum(WalletTransaction.amount), 0)).where(
                                WalletTransaction.user_id == user.id, WalletTransaction.type == "commission")).scalar_one()
    # Historical negative seller refund entries reduce income; positive buyer
    # refunds never count as author income, even when that buyer is a creator.
    refunds = db.session.execute(select(db.func.coalesce(db.func.sum(WalletTransaction.amount), 0)).join(
        Order, WalletTransaction.order_id == Order.id).where(WalletTransaction.user_id == user.id,
        Order.seller_id == user.id, WalletTransaction.type == "refund", WalletTransaction.amount < 0)).scalar_one()
    demo = current_app.config["WALLET_MODE"] == "simulation"
    return jsonify({"balance": float(user.balance), "frozenBalance": float(user.frozen_balance),
                    "totalIncome": float(income), "netIncome": float(income + refunds),
                    "transactions": [x.serialize() for x in txs], "walletMode": current_app.config["WALLET_MODE"],
                    "demoEnabled": demo, "canRecharge": demo, "canWithdraw": demo, "canPay": demo})


@api.post("/api/wallet/recharge")
@jwt_required()
def recharge():
    return jsonify(money.wallet_operation(request_identity(), "recharge", json_object().get("amount"), request.headers.get("Idempotency-Key")))


@api.post("/api/wallet/withdraw")
@jwt_required()
def withdraw():
    return jsonify(money.wallet_operation(request_identity(), "withdraw", json_object().get("amount"), request.headers.get("Idempotency-Key")))


@api.get("/api/toolkits/<int:toolkit_id>/download")
@jwt_required()
def download(toolkit_id):
    identity = request_identity()
    response = None
    try:
        with Session(db.engine) as session, session.begin():
            toolkit = money.lock_toolkit(session, toolkit_id)
            if not toolkit:
                raise APIError("工具包不存在", 404)
            user = money.lock_users(session, [identity.uid])[identity.uid]
            assert_identity(session, user, identity)
            purchased = money.current_purchase(session, user.id, toolkit_id) is not None
            if not is_visible(toolkit, user, purchased):
                raise APIError("工具包不存在", 404)
            if not can_download(toolkit, user, purchased):
                raise APIError("购买后方可下载", 403)
            if not toolkit.resource_path or not safe_stored_name(toolkit.resource_path):
                raise APIError("作者暂未上传资源", 404)
            asset = session.execute(select(FileAsset).where(FileAsset.stored_name == toolkit.resource_path)).scalar_one_or_none()
            if asset and (asset.purpose != "resource" or asset.state not in {"active", "legacy", "retained"}):
                raise APIError("资源不存在", 404)
            # Preparing/opening the response may raise 404/IO. Increment only
            # after a response really exists, once under the Toolkit lock.
            response = send_from_directory(directory("resource"), toolkit.resource_path,
                                           as_attachment=True, download_name=toolkit.resource_name or "resource")
            session.execute(update(Toolkit).where(Toolkit.id == toolkit_id).values(downloads=db.func.coalesce(Toolkit.downloads, 0) + 1))
    except Exception:
        if response:
            response.close()
        raise
    return response


@api.get("/api/admin/stats")
@admin_required
def admin_stats():
    revenue = db.session.execute(select(db.func.coalesce(db.func.sum(Order.amount), 0)).where(Order.status == "paid")).scalar_one()
    return jsonify({"users": User.query.count(), "toolkits": Toolkit.query.count(),
                    "orders": Order.query.filter_by(status="paid").count(), "revenue": float(revenue)})


@api.get("/api/admin/toolkits")
@admin_required
def admin_toolkits():
    return jsonify(_serialize_list(Toolkit.query.order_by(Toolkit.created_at.desc()).all(), current_user()))


@api.get("/api/admin/orders")
@admin_required
def admin_orders():
    rows = Order.query.options(joinedload(Order.toolkit), joinedload(Order.buyer), joinedload(Order.seller)).order_by(Order.created_at.desc()).limit(100).all()
    return jsonify([x.serialize() for x in rows])


@api.post("/api/admin/toolkits/<int:toolkit_id>/featured")
@admin_required
def set_featured(toolkit_id):
    featured = json_object().get("featured")
    if type(featured) is not bool:
        raise APIError("featured 须为 true/false")
    identity = request_identity()
    with Session(db.engine) as session, session.begin():
        toolkit, _, user = _manage_locked(session, toolkit_id, identity)
        if user.role != "admin":
            raise APIError("需要超级管理员权限", 403)
        toolkit.featured = featured
        session.flush()
        result = toolkit.serialize(user.id, user=user)
    return jsonify(result)


@api.post("/api/admin/orders/<int:order_id>/refund")
@admin_required
def refund_order(order_id):
    # No query, financial mutation or historic data repair in this endpoint.
    raise APIError("平台不支持退款", 403, "REFUNDS_DISABLED")


def seed_data():
    if current_app.config["APP_ENV"] == "production" or not current_app.config["DEMO_SEED"]:
        raise RuntimeError("Demo seeding requires explicit DEMO_SEED outside production")
    with Session(db.engine) as session, session.begin():
        if session.execute(select(User.id).limit(1)).first():
            return
        admin = User(username="admin", nickname="知核管理员", password_hash=generate_password_hash(DEMO_PASSWORDS["admin"]), role="admin", balance=Decimal("1000"))
        researcher = User(username="researcher", nickname="林博士", password_hash=generate_password_hash(DEMO_PASSWORDS["researcher"]), role="user", balance=Decimal("288"))
        creator = User(username="creator", nickname="BioLab·周", password_hash=generate_password_hash(DEMO_PASSWORDS["creator"]), role="user", balance=Decimal("1268.50"))
        session.add_all([admin, researcher, creator])
        session.flush()
        samples = [("单细胞 RNA-seq 标准分析工作流", "单细胞", "入门", 0),
                   ("AlphaFold 3 蛋白结构预测实战", "结构生物学", "进阶", 69),
                   ("CRISPR sgRNA 设计与脱靶评估", "基因编辑", "进阶", 39),
                   ("空间转录组数据可视化图谱", "空间组学", "专业", 99),
                   ("GSEA 富集分析与发表级绘图", "生信分析", "入门", 0),
                   ("分子动力学模拟 GROMACS 入门", "结构生物学", "专业", 129)]
        for index, (title, category, level, price) in enumerate(samples):
            toolkit = Toolkit(title=title, slug=f"demo-{index + 1}", summary=f"{title}：可复现的科研实践步骤。",
                              description=f"{title}\n\n演示内容，请替换为经过验证的教程。", category=category, level=level,
                              price=price, author_id=creator.id, featured=index < 3, status="published")
            session.add(toolkit)
            session.flush()
            for j, title in enumerate(["认识工作流与环境准备", "数据准备与质量控制", "核心分析与参数解读", "结果可视化与导出", "常见问题与进阶实践"]):
                session.add(Lesson(toolkit_id=toolkit.id, title=title, content=f"## {title}\n\n演示步骤，请替换为真实说明。", sort_order=j, duration=10, is_preview=j == 0))


if __name__ == "__main__":
    application = create_app()
    if application.config["APP_ENV"] != "development":
        raise RuntimeError("The built-in server is development-only; use WSGI in production")
    application.run(host="127.0.0.1", port=6000, debug=application.config["DEBUG"])
