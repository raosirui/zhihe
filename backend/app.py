"""Flask application composition root and legacy compatibility exports.

Domain routes live in ``api.routes`` and publish persistence lives in
``services``. Importing this module never opens a database connection, creates
storage, or seeds application data.
"""
import re

from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.exceptions import HTTPException

from api import api
from api import routes as _routes  # noqa: F401 - registers handlers on the shared blueprint
from api.support import (
    _access_toolkit,
    _accessible_lesson_raw,
    _manage_locked,
    _purchased,
    _serialize_list,
    _token,
)
from config import build_config
from extensions import db, jwt
from models import (
    ASSET_PATH,
    LESSON_ASSET_PATH,
    BatchJob,
    Favorite,
    FileAsset,
    FileOperation,
    FileReference,
    Lesson,
    LessonProgress,
    Order,
    Purchase,
    RateLimitBucket,
    RateLimitGuard,
    RevokedToken,
    Toolkit,
    ToolkitRating,
    User,
    WalletReceipt,
    WalletTransaction,
    utcnow,
)
from security import (
    APIError,
    account_rate_key,
    admin_required,
    assert_identity,
    client_ip,
    configure_jwt,
    current_user,
    json_object,
    lock_existing_child,
    optional_user,
    optional_user_id,
    positive_id,
    rate_hit,
    rate_hits,
    request_identity,
    text,
)
from access import can_download, can_learn, is_manager, is_visible
from file_service import (
    UploadContext,
    bind_reference,
    directory,
    init_storage,
    lock_assets,
    reclaim_asset,
    recover_file_operations,
    referenced_asset_ids,
    safe_stored_name,
    source_asset,
    unbind_toolkit,
)
from document_converter import (
    convert_document,
    detect_content_type,
    is_allowed_extension,
    render_ppt_preview,
    save_raw_file,
)
from batch_kit import ManifestError, PROMPT_TEXT, build_skill_zip, build_template_zip, load_batch_package
import batch_worker
import money_service as money
from seed_service import DEMO_PASSWORDS, seed_data
from services.toolkit import (
    _canonical_lesson_image,
    _prepare_item_assets,
    _reclaim_lesson_raw,
    parse_lessons_json,
    persist_item,
    toolkit_form_errors,
    validate_toolkit_completeness,
)
from validation import CATEGORIES, IMAGE_EXTENSIONS, LEVELS, MAX_BATCH_RESOURCES, image_source, parse_money, string_field

COMMISSION_RATE = money.COMMISSION_RATE


def _persist_item(item, identity, uploads, toolkit_id=None, skip_existing=False, publish=True):
    """Keep the historical app-level publishing seam for callers and tests."""
    return persist_item(item, identity, uploads, toolkit_id=toolkit_id,
                        skip_existing=skip_existing, publish=publish,
                        reclaim_asset_fn=lambda *args, **kwargs: reclaim_asset(*args, **kwargs))


def init_database():
    """Run only inside an explicitly selected application's context."""
    db.create_all()


def prune_revoked_tokens():
    with Session(db.engine) as session, session.begin():
        session.execute(delete(RevokedToken).where(RevokedToken.expires_at < utcnow()))


def create_app(config_override=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(build_config(config_override))
    db.init_app(app)
    jwt.init_app(app)
    configure_jwt()
    origins = app.config["FRONTEND_ORIGIN"]
    CORS(app, origins=origins if isinstance(origins, list) else [origins], supports_credentials=False,
         allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])
    # Resolve the module-level symbol at request time so legacy monkeypatches of
    # app.reclaim_asset continue to affect the post-commit cleanup path.
    app.extensions["zhihe.reclaim_asset"] = lambda *args, **kwargs: reclaim_asset(*args, **kwargs)
    app.extensions["zhihe.persist_toolkit_item"] = _persist_item
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
        protected = request.path.startswith((LESSON_ASSET_PATH, "/api/lesson-raw/", "/api/lesson-preview/",
                                             "/api/lesson-pdf/", "/api/auth/", "/api/wallet", "/api/me/", "/api/orders", "/api/admin/"))
        resource_preview = request.path.startswith("/api/toolkits/") and request.path.endswith("/preview")
        authenticated = bool(request.headers.get("Authorization"))
        if protected or resource_preview or authenticated or response.status_code >= 400:
            response.headers["Cache-Control"] = ("private, no-store" if request.path.startswith(("/api/lesson-preview/", "/api/lesson-raw/", "/api/lesson-pdf/"))
                                                 else "no-store")
            response.headers["Pragma"] = "no-cache"
        if resource_preview or request.path.startswith((ASSET_PATH, LESSON_ASSET_PATH)):
            response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response

    return app


if __name__ == "__main__":
    application = create_app()
    if application.config["APP_ENV"] != "development":
        raise RuntimeError("The built-in server is development-only; use WSGI in production")
    application.run(host="127.0.0.1", port=6000, debug=application.config["DEBUG"])
