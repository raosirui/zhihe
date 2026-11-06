"""Owned cover, lesson image, and lesson document endpoints."""
import os
import zipfile

from flask import current_app, jsonify, send_file, send_from_directory
from flask_jwt_extended import jwt_required
from sqlalchemy import select

from api import api
from api.support import (_accessible_lesson_raw, _purchased, ASSET_PATH, LESSON_ASSET_PATH)
from extensions import db
from models import FileAsset, FileReference, Lesson, Toolkit
from security import APIError, client_ip, optional_user, rate_hits
from access import can_learn, is_visible
from document_converter import detect_content_type, is_allowed_extension, render_ppt_preview
from file_service import directory, safe_stored_name
from validation import IMAGE_EXTENSIONS


@api.get("/api/uploads/<stored>")
def uploaded_cover(stored):
    user = optional_user()  # Invalid optional credentials still fail, even on public URLs.
    if not safe_stored_name(stored) or os.path.splitext(stored)[1].lower() not in IMAGE_EXTENSIONS:
        raise APIError("资源不存在", 404)
    asset = db.session.execute(select(FileAsset).where(FileAsset.stored_name == stored)).scalar_one_or_none()
    if asset:
        if asset.purpose != "image" or asset.state not in {"active", "legacy", "retained"}:
            raise APIError("资源不存在", 404)
        # Owners always see their own uploads (draft covers are not public refs).
        if user and asset.owner_id == user.id:
            return send_from_directory(directory("image"), stored)
        if not asset.is_public:
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


@api.get("/api/lesson-raw/<int:lesson_id>")
@jwt_required(optional=True)
def download_lesson_raw(lesson_id):
    """Download original document file for an accessible lesson."""
    _, path = _accessible_lesson_raw(lesson_id)
    return send_file(path, as_attachment=True)


@api.get("/api/lesson-preview/<int:lesson_id>")
@jwt_required(optional=True)
def lesson_preview(lesson_id):
    """Data-only PPTX layout, under exactly the original-file permissions."""
    lesson, path = _accessible_lesson_raw(lesson_id)
    if lesson.content_type != "ppt" or not is_allowed_extension(path.name, "ppt"):
        raise APIError("此文档不支持演示文稿预览", 404)
    rate_hits([(f"ppt-preview:{client_ip()}", 30, 60),
               (f"ppt-preview:{client_ip()}", 120, 600)])
    try:
        return jsonify(render_ppt_preview(path))
    except (ValueError, zipfile.BadZipFile):
        raise APIError("演示文稿无法预览，请查看文档内容", 422) from None
