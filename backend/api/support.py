"""Shared helpers used by API route modules."""
from pathlib import Path

from flask import current_app
from flask_jwt_extended import create_access_token
from sqlalchemy import select
from sqlalchemy.orm import selectinload, undefer

from extensions import db
from models import Favorite, FileReference, Lesson, Purchase, Toolkit, ASSET_PATH, LESSON_ASSET_PATH
from security import APIError, assert_identity
from access import is_manager, is_visible
import money_service as money
from document_converter import detect_content_type
from file_service import reclaim_asset as _file_reclaim_asset, safe_stored_name


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
    ids = [item.id for item in items]
    bought = set(db.session.execute(select(Purchase.toolkit_id).where(Purchase.user_id == user.id,
                 Purchase.toolkit_id.in_(ids))).scalars()) if user and ids else set()
    favorites = set(db.session.execute(select(Favorite.toolkit_id).where(Favorite.user_id == user.id,
                   Favorite.toolkit_id.in_(ids))).scalars()) if user and ids else set()
    return [item.serialize(user.id if user else None, user=user, purchased_ids=bought, favorited_ids=favorites)
            for item in items]


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


def persist_toolkit_item(item, identity, uploads, toolkit_id=None, skip_existing=False, publish=True):
    callback = current_app.extensions.get("zhihe.persist_toolkit_item")
    if callback is not None:
        return callback(item, identity, uploads, toolkit_id=toolkit_id,
                        skip_existing=skip_existing, publish=publish)
    from services.toolkit import persist_item
    return persist_item(item, identity, uploads, toolkit_id=toolkit_id,
                        skip_existing=skip_existing, publish=publish)


def reclaim_asset(*args, **kwargs):
    """Honor the legacy app.reclaim_asset monkeypatch seam when configured."""
    callback = current_app.extensions.get("zhihe.reclaim_asset")
    if callback is not None:
        return callback(*args, **kwargs)
    return _file_reclaim_asset(*args, **kwargs)


def _accessible_lesson_raw(lesson_id):
    """Apply the raw-file policy and confine the stored path to this lesson."""
    from security import optional_user
    from access import can_learn

    user = optional_user()
    lesson = db.session.get(Lesson, lesson_id)
    if not lesson:
        raise APIError("原始文件不存在", 404)
    toolkit = lesson.toolkit
    purchased = _purchased(db.session, user, toolkit.id)
    if not is_visible(toolkit, user, purchased) or not can_learn(toolkit, user, purchased, lesson):
        raise APIError("无权访问", 403)
    ref = db.session.execute(select(FileReference).where(
        FileReference.toolkit_id == toolkit.id, FileReference.lesson_id == lesson.id,
        FileReference.usage == "lesson_raw")).scalar_one_or_none()
    if ref:
        asset = ref.asset
        if not asset or asset.purpose != "lesson_raw" or asset.state not in {"active", "legacy", "retained"}:
            raise APIError("原始文件已丢失", 404)
        return lesson, asset
    if not lesson.raw_file_path:
        raise APIError("原始文件不存在", 404)
    base = (Path(current_app.config["UPLOAD_FOLDER"]) / "lesson_raw").resolve()
    path = Path(lesson.raw_file_path)
    if (path.name.startswith(f"lesson_{lesson.id}_") and
            detect_content_type(path.name) and
            not path.is_symlink() and path.parent.resolve() == base and
            path.is_file() and path.resolve().parent == base):
        return lesson, path
    raise APIError("原始文件已丢失", 404)


__all__ = ["_token", "_purchased", "_access_toolkit", "_serialize_list", "_manage_locked",
           "persist_toolkit_item", "_accessible_lesson_raw", "reclaim_asset", "ASSET_PATH",
           "LESSON_ASSET_PATH", "safe_stored_name"]
