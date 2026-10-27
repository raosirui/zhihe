import io
import os
import re
import threading
import time
import uuid
import zipfile
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from urllib.parse import quote_plus

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, get_jwt, get_jwt_identity, jwt_required
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from batch_kit import (
    CATEGORIES, IMAGE_EXTENSIONS, LEVELS, PROMPT_TEXT,
    REJECTED_RESOURCE_EXTENSIONS, ManifestError, build_skill_zip,
    build_template_zip, load_batch_package,
)

load_dotenv()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


app = Flask(__name__)
db_password = quote_plus(os.getenv("MYSQL_PASSWORD", ""))
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    f"mysql+pymysql://{os.getenv('MYSQL_USER', 'root')}:{db_password}@"
    f"{os.getenv('MYSQL_HOST', '127.0.0.1')}:{os.getenv('MYSQL_PORT', '3306')}/"
    f"{os.getenv('MYSQL_DATABASE', 'zhihe')}?charset=utf8mb4",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-only-change-me")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=int(os.getenv("JWT_EXPIRE_DAYS", "1")))
# 教程配图走 <img> 加载，无法带 Authorization 头，允许 query string 携带 token
app.config["JWT_TOKEN_LOCATION"] = ["headers", "query_string"]
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
IMAGE_DIR = os.path.join(app.config["UPLOAD_FOLDER"], "image")
RESOURCE_DIR = os.path.join(app.config["UPLOAD_FOLDER"], "resource")
ASSET_DIR = os.path.join(app.config["UPLOAD_FOLDER"], "asset")  # 付费教程配图，不走公开路由
os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(RESOURCE_DIR, exist_ok=True)
os.makedirs(ASSET_DIR, exist_ok=True)

STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}_[A-Za-z0-9._\-]{1,200}$")
ASSET_PATH = "/api/uploads/"
LESSON_ASSET_PATH = "/api/lesson-asset/"

CORS(app, origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:4000")], supports_credentials=True)
db = SQLAlchemy(app)
jwt = JWTManager(app)

long_text = lambda: db.Text().with_variant(LONGTEXT, "mysql")  # noqa: E731

# 演示账号弱口令告警
DEMO_PASSWORDS = {"admin": "Admin@123", "researcher": "User@123", "creator": "Creator@123"}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    nickname = db.Column(db.String(80), nullable=False)
    role = db.Column(db.Enum("admin", "user"), default="user", nullable=False)
    avatar = db.Column(db.String(500))
    bio = db.Column(db.String(300), default="热爱探索生命科学")
    balance = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    frozen_balance = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    def public(self):
        return {
            "id": self.id, "username": self.username, "nickname": self.nickname,
            "role": self.role, "avatar": self.avatar, "bio": self.bio,
            "balance": float(self.balance or 0), "frozenBalance": float(self.frozen_balance or 0),
            "createdAt": self.created_at.isoformat(),
        }


class Toolkit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(140), unique=True, nullable=False)
    summary = db.Column(db.String(500), nullable=False)
    description = db.Column(long_text(), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    level = db.Column(db.Enum("入门", "进阶", "专业"), default="入门")
    cover = db.Column(db.String(500))
    price = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    author = db.relationship("User", backref="toolkits")
    status = db.Column(db.Enum("draft", "published", "offline"), default="published")
    downloads = db.Column(db.Integer, default=0)
    rating = db.Column(db.Numeric(3, 1), default=5.0)
    featured = db.Column(db.Boolean, default=False)
    tags = db.Column(db.String(300), default="")
    resource_name = db.Column(db.String(255))
    resource_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def serialize(self, user_id=None, detail=False, purchased_ids=None, favorited_ids=None):
        purchased = self.price == 0
        favorited = False
        if user_id and (self.author_id == user_id or self.price > 0):
            if self.author_id == user_id:
                purchased = True
            if not purchased:
                if purchased_ids is not None:
                    purchased = self.id in purchased_ids
                else:
                    purchased = Purchase.query.filter_by(user_id=user_id, toolkit_id=self.id).first() is not None
            if favorited_ids is not None:
                favorited = self.id in favorited_ids
            else:
                favorited = Favorite.query.filter_by(user_id=user_id, toolkit_id=self.id).first() is not None
        data = {
            "id": self.id, "title": self.title, "slug": self.slug, "summary": self.summary,
            "category": self.category, "level": self.level, "cover": self.cover,
            "price": float(self.price), "author": {"id": self.author.id, "nickname": self.author.nickname, "avatar": self.author.avatar},
            "downloads": self.downloads, "rating": float(self.rating), "featured": self.featured,
            "tags": [x for x in self.tags.split(",") if x], "status": self.status,
            "purchased": purchased, "favorited": favorited, "lessonCount": len(self.lessons),
            "createdAt": self.created_at.isoformat(),
        }
        if detail:
            data.update({
                "description": self.description,
                "resourceName": self.resource_name,
                "lessons": [lesson.serialize(purchased) for lesson in sorted(self.lessons, key=lambda x: x.sort_order)],
            })
        return data


class Lesson(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    toolkit_id = db.Column(db.Integer, db.ForeignKey("toolkit.id", ondelete="CASCADE"), nullable=False)
    toolkit = db.relationship("Toolkit", backref=db.backref("lessons", cascade="all, delete-orphan"))
    title = db.Column(db.String(160), nullable=False)
    content = db.Column(long_text(), nullable=False)
    # 外链图片直接存 URL；平台上传的图片存 "file:<stored_name>"，访问需解锁校验
    image = db.Column(db.String(500))
    sort_order = db.Column(db.Integer, default=0)
    duration = db.Column(db.Integer, default=10)
    is_preview = db.Column(db.Boolean, default=False)

    def serialize(self, unlocked=False):
        can_view = unlocked or self.is_preview
        image = None
        if can_view and self.image:
            if self.image.startswith("file:"):
                token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip() if request else ""
                image = f"{LESSON_ASSET_PATH}{self.image[5:]}" + (f"?t={token}" if token else "")
            else:
                image = self.image
        return {
            "id": self.id, "title": self.title, "sortOrder": self.sort_order,
            "duration": self.duration, "isPreview": self.is_preview, "locked": not can_view,
            "content": self.content if can_view else None, "image": image,
        }


class Favorite(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "toolkit_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    toolkit_id = db.Column(db.Integer, db.ForeignKey("toolkit.id"), nullable=False)
    toolkit = db.relationship("Toolkit")
    created_at = db.Column(db.DateTime, default=utcnow)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(40), unique=True, nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    buyer = db.relationship("User", foreign_keys=[buyer_id])
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    seller = db.relationship("User", foreign_keys=[seller_id])
    toolkit_id = db.Column(db.Integer, db.ForeignKey("toolkit.id"), nullable=False)
    toolkit = db.relationship("Toolkit")
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    commission = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.Enum("pending", "paid", "cancelled", "refunded"), default="pending")
    paid_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    def serialize(self):
        return {
            "id": self.id, "orderNo": self.order_no, "amount": float(self.amount),
            "commission": float(self.commission), "status": self.status,
            "toolkit": {"id": self.toolkit.id, "title": self.toolkit.title, "cover": self.toolkit.cover},
            "seller": self.seller.nickname, "buyer": self.buyer.nickname,
            "createdAt": self.created_at.isoformat(),
            "paidAt": self.paid_at.isoformat() if self.paid_at else None,
        }


class Purchase(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "toolkit_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    toolkit_id = db.Column(db.Integer, db.ForeignKey("toolkit.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class WalletTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    type = db.Column(db.Enum("recharge", "purchase", "commission", "withdrawal", "refund"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    balance_after = db.Column(db.Numeric(12, 2), nullable=False)
    note = db.Column(db.String(255))
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    created_at = db.Column(db.DateTime, default=utcnow)

    def serialize(self):
        return {"id": self.id, "type": self.type, "amount": float(self.amount), "balanceAfter": float(self.balance_after), "note": self.note, "createdAt": self.created_at.isoformat()}


class LessonProgress(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "lesson_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id", ondelete="CASCADE"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class RevokedToken(db.Model):
    jti = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


@jwt.token_in_blocklist_loader
def token_revoked(_header, payload):
    jti = payload.get("jti")
    if not jti:
        return True
    record = db.session.get(RevokedToken, jti)
    return record is not None


@jwt.unauthorized_loader
def unauthorized(_reason):
    return jsonify({"message": "请先登录"}), 401


@jwt.invalid_token_loader
def invalid_token(_reason):
    return jsonify({"message": "登录状态无效，请重新登录"}), 401


@jwt.expired_token_loader
def expired_token(_header, _payload):
    return jsonify({"message": "登录已过期，请重新登录"}), 401


@jwt.revoked_token_loader
def revoked_token(_header, _payload):
    return jsonify({"message": "登录已失效，请重新登录"}), 401


def current_user():
    identity = get_jwt_identity()
    return db.session.get(User, int(identity)) if identity else None


def admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or user.role != "admin":
            return jsonify({"message": "需要超级管理员权限"}), 403
        return fn(*args, **kwargs)
    return wrapper


def optional_user_id():
    try:
        from flask_jwt_extended import verify_jwt_in_request
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        return int(identity) if identity else None
    except Exception:
        return None


# ------------------------------------------------------------ 简易速率限制
_rate_lock = threading.Lock()
_rate_buckets = defaultdict(deque)


def rate_hit(key, limit, window=60):
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


@app.errorhandler(413)
def too_large(_):
    return jsonify({"message": "文件不能超过 100MB"}), 413


@app.errorhandler(404)
def not_found(_):
    return jsonify({"message": "接口或资源不存在"}), 404


@app.errorhandler(405)
def not_allowed(_):
    return jsonify({"message": "请求方法不被支持"}), 405


@app.errorhandler(SQLAlchemyError)
def db_error(error):
    db.session.rollback()
    app.logger.exception("database error")
    return jsonify({"message": "数据保存失败，请检查输入内容"}), 500


@app.after_request
def harden_asset_responses(response):
    if request.path.startswith((ASSET_PATH, LESSON_ASSET_PATH)):
        # 上传文件（含 svg）一律禁止脚本执行与类型嗅探
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response.headers["Cache-Control"] = "private, max-age=3600"
    return response


# ------------------------------------------------------------ 通用工具

def safe_stored_name(stored):
    return bool(STORED_NAME_RE.match(stored))


def store_upload(kind, name, data):
    directory = {"image": IMAGE_DIR, "asset": ASSET_DIR}.get(kind, RESOURCE_DIR)
    stored = f"{uuid.uuid4().hex}_{secure_filename(name) or 'file'}"
    with open(os.path.join(directory, stored), "wb") as fh:
        fh.write(data)
    return stored


def toolkit_form_errors(form):
    """单个发布与批量共用的字段校验，返回 (错误消息, 清洗后的字段)。"""
    title = (form.get("title") or "").strip()
    if not title:
        return "标题不能为空", None
    if len(title) > 120:
        return "标题不能超过 120 字", None
    description = (form.get("description") or "").strip()
    if not description:
        return "介绍不能为空", None
    summary = (form.get("summary") or "").strip()[:500] or description[:120]
    category = form.get("category") or "生信分析"
    if category not in CATEGORIES:
        return f"分类须为：{' / '.join(CATEGORIES)}", None
    level = form.get("level") or "入门"
    if level not in LEVELS:
        return f"难度须为：{' / '.join(LEVELS)}", None
    try:
        price = Decimal(str(form.get("price") or 0)).quantize(Decimal("0.01"))
    except Exception:
        return "定价必须是有效数字", None
    if price < 0 or price > 9999:
        return "定价须在 0～9999 元之间", None
    tags = str(form.get("tags") or "").strip()[:300]
    cover = (form.get("cover") or "").strip()[:500] or None
    return None, {"title": title, "summary": summary, "description": description,
                  "category": category, "level": level, "price": price, "tags": tags, "cover": cover}


def parse_lessons_json(raw):
    import json
    try:
        lessons = json.loads(raw or "[]")
    except ValueError:
        return "教程步骤 JSON 格式不正确", None
    if not isinstance(lessons, list) or len(lessons) > 50:
        return "教程步骤须为不超过 50 项的数组", None
    cleaned = []
    for index, lesson in enumerate(lessons):
        if not isinstance(lesson, dict):
            return f"步骤 {index + 1} 格式不正确", None
        try:
            duration = int(lesson.get("duration", 10))
        except (TypeError, ValueError):
            return f"步骤 {index + 1} 的学习时长无效", None
        cleaned.append({
            "title": (lesson.get("title") or f"步骤 {index + 1}")[:160],
            "content": str(lesson.get("content") or ""),
            "image": (str(lesson.get("image"))[:500] if lesson.get("image") else None),
            "duration": max(1, min(600, duration)),
        })
    return None, cleaned


# ------------------------------------------------------------ 认证

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "zhihe-api"})


@app.get("/api/stats")
def public_stats():
    return jsonify({
        "toolkits": Toolkit.query.filter_by(status="published").count(),
        "users": User.query.count(),
    })


@app.post("/api/auth/login")
def login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    if not rate_hit(f"login|{request.remote_addr}|{username}", 8):
        return jsonify({"message": "尝试次数过多，请 1 分钟后再试"}), 429
    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, data.get("password", "")):
        return jsonify({"message": "账号或密码不正确"}), 401
    token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return jsonify({"token": token, "user": user.public()})


@app.post("/api/auth/register")
def register():
    data = request.get_json() or {}
    if not rate_hit(f"register|{request.remote_addr}", 5):
        return jsonify({"message": "注册过于频繁，请稍后再试"}), 429
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    nickname = (data.get("nickname") or "").strip() or username
    if len(username) < 3 or len(username) > 50 or not re.fullmatch(r"[\w.\-·]+", username):
        return jsonify({"message": "账号需为 3～50 位字母、数字或 _.- 字符"}), 400
    if len(password) < 8:
        return jsonify({"message": "密码至少 8 位"}), 400
    if len(nickname) > 80:
        return jsonify({"message": "昵称不能超过 80 字"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"message": "账号已存在"}), 409
    user = User(username=username, nickname=nickname,
                password_hash=generate_password_hash(password), role="user")
    db.session.add(user)
    db.session.commit()
    token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return jsonify({"token": token, "user": user.public()}), 201


@app.get("/api/auth/me")
@jwt_required()
def me():
    return jsonify(current_user().public())


def revoke_current_token():
    payload = get_jwt()
    jti = payload.get("jti")
    if jti and db.session.get(RevokedToken, jti) is None:
        expires_at = datetime.fromtimestamp(payload["exp"], timezone.utc).replace(tzinfo=None)
        db.session.add(RevokedToken(jti=jti, user_id=int(get_jwt_identity()), expires_at=expires_at))


@app.post("/api/auth/logout")
@jwt_required()
def logout():
    revoke_current_token()
    db.session.commit()
    return jsonify({"message": "已退出登录"})


@app.put("/api/auth/password")
@jwt_required()
def change_password():
    data = request.get_json() or {}
    user = current_user()
    if not check_password_hash(user.password_hash, data.get("oldPassword", "")):
        return jsonify({"message": "原密码不正确"}), 400
    new_password = data.get("newPassword") or ""
    if len(new_password) < 8:
        return jsonify({"message": "新密码至少 8 位"}), 400
    user.password_hash = generate_password_hash(new_password)
    revoke_current_token()  # 改密后强制重新登录
    db.session.commit()
    return jsonify({"message": "密码已修改，请重新登录"})


# ------------------------------------------------------------ 工具包

@app.get("/api/toolkits")
def list_toolkits():
    user_id = optional_user_id()
    query = Toolkit.query.filter(Toolkit.status == "published")
    keyword = request.args.get("q", "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(db.or_(Toolkit.title.like(like), Toolkit.summary.like(like), Toolkit.tags.like(like)))
    category = request.args.get("category")
    if category and category != "全部":
        query = query.filter_by(category=category)
    price = request.args.get("price")
    if price == "free":
        query = query.filter(Toolkit.price == 0)
    if price == "paid":
        query = query.filter(Toolkit.price > 0)
    sort = request.args.get("sort", "recommended")
    if sort == "newest":
        query = query.order_by(Toolkit.created_at.desc())
    elif sort == "popular":
        query = query.order_by(Toolkit.downloads.desc())
    elif sort == "price":
        query = query.order_by(Toolkit.price.asc())
    else:
        query = query.order_by(Toolkit.featured.desc(), Toolkit.downloads.desc())
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 60))))
    except ValueError:
        page, per_page = 1, 60
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    items = pagination.items
    purchased_ids = favorited_ids = None
    if user_id:
        ids = [t.id for t in items]
        purchased_ids = {p.toolkit_id for p in Purchase.query.filter(
            Purchase.user_id == user_id, Purchase.toolkit_id.in_(ids)).all()} if ids else set()
        favorited_ids = {f.toolkit_id for f in Favorite.query.filter(
            Favorite.user_id == user_id, Favorite.toolkit_id.in_(ids)).all()} if ids else set()
        purchased_ids |= {t.id for t in items if t.author_id == user_id}
    return jsonify({
        "items": [x.serialize(user_id, purchased_ids=purchased_ids, favorited_ids=favorited_ids) for x in items],
        "total": pagination.total, "page": page, "pages": pagination.pages,
    })


@app.get("/api/toolkits/<int:toolkit_id>")
def get_toolkit(toolkit_id):
    toolkit = db.session.get(Toolkit, toolkit_id)
    user_id = optional_user_id()
    if toolkit and toolkit.status != "published" and user_id:
        user = db.session.get(User, user_id)
        if user and (user.role == "admin" or toolkit.author_id == user_id):
            return jsonify(toolkit.serialize(user_id, detail=True))
    if not toolkit or toolkit.status != "published":
        return jsonify({"message": "工具包不存在"}), 404
    return jsonify(toolkit.serialize(user_id, detail=True))


@app.post("/api/toolkits")
@jwt_required()
def create_toolkit():
    user = current_user()
    data = request.form
    error, fields = toolkit_form_errors(data)
    if error:
        return jsonify({"message": error}), 400
    lessons_error, lessons = parse_lessons_json(data.get("lessons"))
    if lessons_error:
        return jsonify({"message": lessons_error}), 400
    for lesson in lessons:
        image = lesson["image"]
        if image and not image.startswith(("http://", "https://", "/api/uploads/")):
            return jsonify({"message": f"步骤图片地址无效：{image[:60]}"}), 400
    resource_file = request.files.get("resource")
    if resource_file and resource_file.filename:
        suffix = os.path.splitext(resource_file.filename)[1].lower()
        if suffix in REJECTED_RESOURCE_EXTENSIONS:
            return jsonify({"message": f"不支持上传 {suffix} 类型的资源文件"}), 400
    slug = f"{uuid.uuid4().hex[:8]}-{secure_filename(fields['title'])[:50] or 'toolkit'}"
    toolkit = Toolkit(
        title=fields["title"], slug=slug, summary=fields["summary"], description=fields["description"],
        category=fields["category"], level=fields["level"], price=fields["price"],
        author_id=user.id, tags=fields["tags"], cover=fields["cover"] or None, status="published",
    )
    if resource_file and resource_file.filename:
        filename = store_upload("resource", resource_file.filename, resource_file.read())
        toolkit.resource_name, toolkit.resource_path = secure_filename(resource_file.filename) or "resource", filename
    db.session.add(toolkit)
    db.session.flush()
    for index, lesson in enumerate(lessons):
        db.session.add(Lesson(
            toolkit_id=toolkit.id, title=lesson["title"], content=lesson["content"],
            image=lesson["image"], duration=lesson["duration"], sort_order=index,
            is_preview=index == 0,
        ))
    db.session.commit()
    return jsonify(toolkit.serialize(user.id, detail=True)), 201


@app.put("/api/toolkits/<int:toolkit_id>/status")
@jwt_required()
def update_status(toolkit_id):
    user = current_user()
    toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit:
        return jsonify({"message": "工具包不存在"}), 404
    status = (request.get_json() or {}).get("status")
    if status not in ("published", "offline"):
        return jsonify({"message": "状态无效"}), 400
    if user.role == "admin":
        pass
    elif toolkit.author_id == user.id:
        if status != "offline":
            return jsonify({"message": "作者只能下架自己的工具包"}), 403
    else:
        return jsonify({"message": "需要超级管理员权限"}), 403
    toolkit.status = status
    db.session.commit()
    return jsonify(toolkit.serialize(user.id))


@app.delete("/api/toolkits/<int:toolkit_id>")
@jwt_required()
def delete_toolkit(toolkit_id):
    user = current_user()
    toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit:
        return jsonify({"message": "工具包不存在"}), 404
    if user.role != "admin" and toolkit.author_id != user.id:
        return jsonify({"message": "只能删除自己发布的工具包"}), 403
    referenced = Order.query.filter_by(toolkit_id=toolkit_id).first()
    if referenced:
        toolkit.status = "offline"
        db.session.commit()
        return jsonify({"message": "该工具包存在订单记录，已改为下架", "mode": "offline"})
    lesson_ids = [x.id for x in Lesson.query.filter_by(toolkit_id=toolkit_id).all()]
    if lesson_ids:
        LessonProgress.query.filter(LessonProgress.lesson_id.in_(lesson_ids)).delete(synchronize_session=False)
    Favorite.query.filter_by(toolkit_id=toolkit_id).delete(synchronize_session=False)
    for stored, directory in ((toolkit.resource_path, RESOURCE_DIR),
                              (toolkit.cover.replace(ASSET_PATH, "") if (toolkit.cover or "").startswith(ASSET_PATH) else None, IMAGE_DIR)):
        if stored and safe_stored_name(stored):
            try:
                os.remove(os.path.join(directory, stored))
            except OSError:
                pass
    for lesson in list(toolkit.lessons):
        if (lesson.image or "").startswith("file:"):
            stored = lesson.image[5:]
            if safe_stored_name(stored):
                try:
                    os.remove(os.path.join(ASSET_DIR, stored))
                except OSError:
                    pass
    db.session.delete(toolkit)
    db.session.commit()
    return jsonify({"message": "工具包已删除", "mode": "deleted"})


# ------------------------------------------------------------ 静态资源与付费配图

@app.get("/api/uploads/<stored>")
def uploaded_cover(stored):
    if not safe_stored_name(stored) or os.path.splitext(stored)[1].lower() not in IMAGE_EXTENSIONS:
        return jsonify({"message": "资源不存在"}), 404
    return send_from_directory(IMAGE_DIR, stored)


@app.get("/api/lesson-asset/<stored>")
def lesson_asset(stored):
    if not safe_stored_name(stored) or os.path.splitext(stored)[1].lower() not in IMAGE_EXTENSIONS:
        return jsonify({"message": "资源不存在"}), 404
    lesson = Lesson.query.filter_by(image=f"file:{stored}").first()
    if not lesson:
        return jsonify({"message": "资源不存在"}), 404
    toolkit = lesson.toolkit
    if lesson.is_preview or toolkit.price == 0:
        return send_from_directory(IMAGE_DIR, stored)
    user_id = optional_user_id()
    if not user_id:
        return jsonify({"message": "请先登录"}), 401
    user = db.session.get(User, user_id)
    unlocked = (toolkit.author_id == user_id or user.role == "admin"
                or Purchase.query.filter_by(user_id=user_id, toolkit_id=toolkit.id).first() is not None)
    if not unlocked:
        return jsonify({"message": "购买后方可查看"}), 403
    return send_from_directory(ASSET_DIR, stored)


# ------------------------------------------------------------ 批量上传

@app.get("/api/batch/template")
def batch_template():
    return send_file(build_template_zip(), mimetype="application/zip",
                     as_attachment=True, download_name="zhihe-standard-template.zip")


@app.get("/api/batch/skill")
def batch_skill():
    return send_file(build_skill_zip(), mimetype="application/zip",
                     as_attachment=True, download_name="zhihe-toolkit-converter-skill.zip")


@app.get("/api/batch/prompt")
def batch_prompt():
    return jsonify({"text": PROMPT_TEXT})


@app.post("/api/batch/upload")
@jwt_required()
def batch_upload():
    user = current_user()
    if not rate_hit(f"batch|{user.id}", 10, window=600):
        return jsonify({"message": "批量上传过于频繁，请稍后再试"}), 429
    file = request.files.get("package")
    if not file:
        return jsonify({"message": "请选择要上传的 ZIP 文件"}), 400
    try:
        zf = zipfile.ZipFile(io.BytesIO(file.read()))
    except zipfile.BadZipFile:
        return jsonify({"message": "文件不是有效的 ZIP 压缩包"}), 400
    with zf:
        try:
            items, errors = load_batch_package(zf)
        except ManifestError as exc:
            return jsonify({"message": str(exc)}), 400
    results = list(errors)
    for item in items:
        existing = Toolkit.query.filter_by(author_id=user.id, title=item["title"]).first()
        if existing:
            results.append({"index": item["index"], "title": item["title"], "status": "skipped",
                            "toolkitId": existing.id, "error": "已有同名工具包，已跳过"})
            continue
        try:
            slug = f"{uuid.uuid4().hex[:8]}-{secure_filename(item['title'])[:50] or 'toolkit'}"
            cover = item["cover_url"]
            if item["cover_bytes"] is not None:
                cover = f"{ASSET_PATH}{store_upload('image', item['cover_name'], item['cover_bytes'])}"
            toolkit = Toolkit(
                title=item["title"], slug=slug, summary=item["summary"], description=item["description"],
                category=item["category"], level=item["level"], price=item["price"],
                author_id=user.id, tags=item["tags"],
                featured=False,  # 推荐位仅管理员可设置
                cover=cover, status="published",
            )
            if item["resource_bytes"] is not None:
                toolkit.resource_name = item["resource_name"]
                toolkit.resource_path = store_upload("resource", item["resource_name"], item["resource_bytes"])
            db.session.add(toolkit)
            db.session.flush()
            for index, lesson in enumerate(item["lessons"]):
                image = lesson["image_url"]
                if lesson["image_bytes"] is not None:
                    image = f"file:{store_upload('asset', lesson['image_name'], lesson['image_bytes'])}"
                db.session.add(Lesson(
                    toolkit_id=toolkit.id, title=lesson["title"], content=lesson["content"],
                    image=image, duration=lesson["duration"], sort_order=index, is_preview=lesson["preview"],
                ))
            db.session.commit()
            results.append({"index": item["index"], "title": item["title"], "status": "created",
                            "toolkitId": toolkit.id, "lessonCount": len(item["lessons"])})
        except SQLAlchemyError as exc:
            db.session.rollback()
            results.append({"index": item["index"], "title": item["title"], "status": "failed",
                            "error": f"创建失败：{type(exc).__name__}"})
    results.sort(key=lambda x: x["index"])
    created = sum(1 for x in results if x["status"] == "created")
    return jsonify({"total": len(results), "created": created, "failed": len(results) - created, "results": results})


# ------------------------------------------------------------ 用户内容

@app.post("/api/toolkits/<int:toolkit_id>/favorite")
@jwt_required()
def toggle_favorite(toolkit_id):
    user = current_user()
    toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit:
        return jsonify({"message": "工具包不存在"}), 404
    favorite = Favorite.query.filter_by(user_id=user.id, toolkit_id=toolkit_id).first()
    if favorite:
        db.session.delete(favorite)
        active = False
    else:
        db.session.add(Favorite(user_id=user.id, toolkit_id=toolkit_id))
        active = True
    db.session.commit()
    return jsonify({"favorited": active})


@app.get("/api/me/favorites")
@jwt_required()
def favorites():
    user = current_user()
    rows = Favorite.query.filter_by(user_id=user.id).order_by(Favorite.created_at.desc()).all()
    return jsonify([x.toolkit.serialize(user.id) for x in rows])


@app.get("/api/me/toolkits")
@jwt_required()
def my_toolkits():
    user = current_user()
    items = Toolkit.query.filter_by(author_id=user.id).order_by(Toolkit.created_at.desc()).all()
    return jsonify([x.serialize(user.id) for x in items])


@app.post("/api/lessons/<int:lesson_id>/complete")
@jwt_required()
def complete_lesson(lesson_id):
    user = current_user()
    lesson = db.session.get(Lesson, lesson_id)
    if not lesson:
        return jsonify({"message": "步骤不存在"}), 404
    exists = LessonProgress.query.filter_by(user_id=user.id, lesson_id=lesson_id).first()
    if not exists:
        db.session.add(LessonProgress(user_id=user.id, lesson_id=lesson_id))
        db.session.commit()
    return jsonify({"completed": True})


@app.get("/api/me/lesson-progress")
@jwt_required()
def lesson_progress():
    user = current_user()
    toolkit_id = request.args.get("toolkit_id", type=int)
    query = LessonProgress.query.filter_by(user_id=user.id)
    if toolkit_id:
        query = query.join(Lesson, LessonProgress.lesson_id == Lesson.id).filter(Lesson.toolkit_id == toolkit_id)
    return jsonify([p.lesson_id for p in query.all()])


# ------------------------------------------------------------ 交易

COMMISSION_RATE = Decimal("0.20")


@app.post("/api/orders")
@jwt_required()
def create_order():
    user = current_user()
    data = request.get_json() or {}
    if not isinstance(data.get("toolkitId"), int):
        return jsonify({"message": "参数无效"}), 400
    toolkit = db.session.get(Toolkit, data["toolkitId"])
    if not toolkit or toolkit.status != "published":
        return jsonify({"message": "工具包不可购买"}), 404
    if toolkit.author_id == user.id:
        return jsonify({"message": "不能购买自己发布的工具包"}), 400
    if toolkit.price <= 0:
        return jsonify({"message": "免费工具包无需购买"}), 400
    if Purchase.query.filter_by(user_id=user.id, toolkit_id=toolkit.id).first():
        return jsonify({"message": "你已购买此工具包"}), 409
    existing = Order.query.filter_by(buyer_id=user.id, toolkit_id=toolkit.id, status="pending").first()
    if existing:
        return jsonify(existing.serialize())
    order = Order(
        order_no=f"ZH{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}",
        buyer_id=user.id, seller_id=toolkit.author_id, toolkit_id=toolkit.id,
        amount=toolkit.price, commission=(toolkit.price * COMMISSION_RATE).quantize(Decimal("0.01")),
    )
    db.session.add(order)
    db.session.commit()
    return jsonify(order.serialize()), 201


@app.post("/api/orders/<int:order_id>/pay")
@jwt_required()
def pay_order(order_id):
    user_id = int(get_jwt_identity())
    # 行锁买家与卖家（按 id 排序加锁，避免死锁），杜绝并发余额竞态
    order = db.session.query(Order).filter_by(id=order_id).with_for_update().first()
    if not order:
        return jsonify({"message": "订单不存在"}), 404
    if order.buyer_id != user_id:
        return jsonify({"message": "无权支付他人订单"}), 403
    if order.status != "pending":
        return jsonify({"message": "订单状态不可支付"}), 400
    if order.amount <= 0:
        return jsonify({"message": "订单金额异常"}), 400
    for uid in sorted({order.buyer_id, order.seller_id}):
        db.session.query(User).filter_by(id=uid).with_for_update().first()
    user = db.session.get(User, order.buyer_id)
    if user.balance < order.amount:
        return jsonify({"message": "余额不足，请先充值"}), 400
    seller = db.session.get(User, order.seller_id)
    user.balance -= order.amount
    seller.balance += order.commission
    order.status, order.paid_at = "paid", utcnow()
    db.session.add(Purchase(user_id=user.id, toolkit_id=order.toolkit_id, order_id=order.id))
    db.session.add(WalletTransaction(user_id=user.id, type="purchase", amount=-order.amount,
                                     balance_after=user.balance, note=f"购买《{order.toolkit.title}》", order_id=order.id))
    db.session.add(WalletTransaction(user_id=seller.id, type="commission", amount=order.commission,
                                     balance_after=seller.balance, note=f"《{order.toolkit.title}》销售分成", order_id=order.id))
    db.session.commit()
    return jsonify({"order": order.serialize(), "user": user.public()})


@app.post("/api/orders/<int:order_id>/cancel")
@jwt_required()
def cancel_order(order_id):
    order = Order.query.filter_by(id=order_id, buyer_id=current_user().id).first()
    if not order or order.status != "pending":
        return jsonify({"message": "订单不可取消"}), 400
    order.status = "cancelled"
    db.session.commit()
    return jsonify(order.serialize())


@app.get("/api/me/orders")
@jwt_required()
def my_orders():
    rows = Order.query.filter_by(buyer_id=current_user().id).order_by(Order.created_at.desc()).all()
    return jsonify([x.serialize() for x in rows])


# ------------------------------------------------------------ 钱包

@app.get("/api/wallet")
@jwt_required()
def wallet():
    user = current_user()
    txs = WalletTransaction.query.filter_by(user_id=user.id).order_by(WalletTransaction.created_at.desc()).limit(100).all()
    income = db.session.query(db.func.sum(WalletTransaction.amount)).filter_by(user_id=user.id, type="commission").scalar() or 0
    return jsonify({"balance": float(user.balance), "frozenBalance": float(user.frozen_balance),
                    "totalIncome": float(income), "transactions": [x.serialize() for x in txs]})


@app.post("/api/wallet/recharge")
@jwt_required()
def recharge():
    user_id = int(get_jwt_identity())
    db.session.query(User).filter_by(id=user_id).with_for_update().first()
    user = db.session.get(User, user_id)
    try:
        amount = Decimal(str((request.get_json() or {}).get("amount", 0))).quantize(Decimal("0.01"))
    except Exception:
        return jsonify({"message": "金额无效"}), 400
    if amount <= 0 or amount > 50000:
        return jsonify({"message": "单次充值金额须在 0.01～50000 元"}), 400
    user.balance += amount
    db.session.add(WalletTransaction(user_id=user.id, type="recharge", amount=amount,
                                     balance_after=user.balance, note="钱包充值（模拟支付）"))
    db.session.commit()
    return jsonify(user.public())


@app.post("/api/wallet/withdraw")
@jwt_required()
def withdraw():
    user_id = int(get_jwt_identity())
    db.session.query(User).filter_by(id=user_id).with_for_update().first()
    user = db.session.get(User, user_id)
    try:
        amount = Decimal(str((request.get_json() or {}).get("amount", 0))).quantize(Decimal("0.01"))
    except Exception:
        return jsonify({"message": "金额无效"}), 400
    if amount < 10 or amount > user.balance:
        return jsonify({"message": "提现至少 10 元且不能超过可用余额"}), 400
    user.balance -= amount
    db.session.add(WalletTransaction(user_id=user.id, type="withdrawal", amount=-amount,
                                     balance_after=user.balance, note="余额提现（模拟到账）"))
    db.session.commit()
    return jsonify(user.public())


# ------------------------------------------------------------ 下载

@app.get("/api/toolkits/<int:toolkit_id>/download")
@jwt_required()
def download(toolkit_id):
    user = current_user()
    toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit:
        return jsonify({"message": "工具包不存在"}), 404
    unlocked = (toolkit.price == 0 or toolkit.author_id == user.id or user.role == "admin"
                or Purchase.query.filter_by(user_id=user.id, toolkit_id=toolkit_id).first())
    if not unlocked:
        return jsonify({"message": "购买后方可下载"}), 403
    if not toolkit.resource_path:
        return jsonify({"message": "作者暂未上传资源"}), 404
    toolkit.downloads += 1
    db.session.commit()
    return send_from_directory(RESOURCE_DIR, toolkit.resource_path,
                               as_attachment=True, download_name=toolkit.resource_name)


# ------------------------------------------------------------ 管理端

@app.get("/api/admin/stats")
@admin_required
def admin_stats():
    revenue = db.session.query(db.func.sum(Order.amount)).filter_by(status="paid").scalar() or 0
    return jsonify({"users": User.query.count(), "toolkits": Toolkit.query.count(),
                    "orders": Order.query.filter_by(status="paid").count(), "revenue": float(revenue)})


@app.get("/api/admin/toolkits")
@admin_required
def admin_toolkits():
    user = current_user()
    items = Toolkit.query.order_by(Toolkit.created_at.desc()).all()
    return jsonify([x.serialize(user.id) for x in items])


@app.get("/api/admin/orders")
@admin_required
def admin_orders():
    rows = Order.query.order_by(Order.created_at.desc()).limit(100).all()
    return jsonify([x.serialize() for x in rows])


@app.post("/api/admin/toolkits/<int:toolkit_id>/featured")
@admin_required
def set_featured(toolkit_id):
    toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit:
        return jsonify({"message": "工具包不存在"}), 404
    toolkit.featured = bool((request.get_json() or {}).get("featured"))
    db.session.commit()
    return jsonify(toolkit.serialize(current_user().id))


@app.post("/api/admin/orders/<int:order_id>/refund")
@admin_required
def refund_order(order_id):
    order = db.session.query(Order).filter_by(id=order_id).with_for_update().first()
    if not order:
        return jsonify({"message": "订单不存在"}), 404
    if order.status != "paid":
        return jsonify({"message": "仅已支付订单可退款"}), 400
    for uid in sorted({order.buyer_id, order.seller_id}):
        db.session.query(User).filter_by(id=uid).with_for_update().first()
    buyer = db.session.get(User, order.buyer_id)
    seller = db.session.get(User, order.seller_id)
    buyer.balance += order.amount
    seller.balance -= order.commission  # 收回分成，允许为负的中间态由余额保证
    order.status = "refunded"
    Purchase.query.filter_by(order_id=order.id).delete()
    db.session.add(WalletTransaction(user_id=buyer.id, type="refund", amount=order.amount,
                                     balance_after=buyer.balance, note=f"《{order.toolkit.title}》订单退款", order_id=order.id))
    db.session.add(WalletTransaction(user_id=seller.id, type="refund", amount=-order.commission,
                                     balance_after=seller.balance, note=f"《{order.toolkit.title}》分成收回", order_id=order.id))
    db.session.commit()
    return jsonify(order.serialize())


def prune_revoked_tokens():
    RevokedToken.query.filter(RevokedToken.expires_at < utcnow()).delete(synchronize_session=False)
    db.session.commit()


def seed_data():
    if User.query.count():
        return
    admin = User(username="admin", nickname="知核管理员", password_hash=generate_password_hash(DEMO_PASSWORDS["admin"]),
                 role="admin", balance=Decimal("1000"), bio="维护知识的秩序")
    researcher = User(username="researcher", nickname="林博士", password_hash=generate_password_hash(DEMO_PASSWORDS["researcher"]),
                      role="user", balance=Decimal("288"), bio="单细胞转录组与肿瘤微环境")
    creator = User(username="creator", nickname="BioLab·周", password_hash=generate_password_hash(DEMO_PASSWORDS["creator"]),
                   role="user", balance=Decimal("1268.50"), bio="生物信息学工具作者")
    db.session.add_all([admin, researcher, creator])
    db.session.flush()
    samples = [
        ("单细胞 RNA-seq 标准分析工作流", "从原始矩阵到细胞注释，快速掌握 Seurat 标准分析流程。", "单细胞", "入门", 0, True, "Seurat,RNA-seq,R语言", "https://images.unsplash.com/photo-1576086213369-97a306d36557?auto=format&fit=crop&w=1200&q=85"),
        ("AlphaFold 3 蛋白结构预测实战", "配置环境、准备输入并解读置信度，完成蛋白复合物结构预测。", "结构生物学", "进阶", 69, True, "AlphaFold,蛋白质,AI", "https://images.unsplash.com/photo-1559757175-0eb30cd8c063?auto=format&fit=crop&w=1200&q=85"),
        ("CRISPR sgRNA 设计与脱靶评估", "一站式完成靶点选择、引物设计与全基因组脱靶风险评估。", "基因编辑", "进阶", 39, True, "CRISPR,sgRNA,基因编辑", "https://images.unsplash.com/photo-1532187863486-abf9dbad1b69?auto=format&fit=crop&w=1200&q=85"),
        ("空间转录组数据可视化图谱", "使用 Scanpy 与 Squidpy 构建高质量空间表达和邻域分析图谱。", "空间组学", "专业", 99, False, "空间转录组,Scanpy,Python", "https://images.unsplash.com/photo-1628595351029-c2bf17511435?auto=format&fit=crop&w=1200&q=85"),
        ("GSEA 富集分析与发表级绘图", "规范完成基因集富集分析，并输出可直接用于论文的图表。", "生信分析", "入门", 0, False, "GSEA,富集分析,绘图", "https://images.unsplash.com/photo-1583912267550-d44c9c800010?auto=format&fit=crop&w=1200&q=85"),
        ("分子动力学模拟 GROMACS 入门", "蛋白体系构建、能量最小化到轨迹分析的完整实操。", "结构生物学", "专业", 129, False, "GROMACS,分子动力学,蛋白质", "https://images.unsplash.com/photo-1518152006812-edab29b069ac?auto=format&fit=crop&w=1200&q=85"),
    ]
    for i, item in enumerate(samples):
        title, summary, category, level, price, featured, tags, cover = item
        toolkit = Toolkit(title=title, slug=f"demo-{i+1}", summary=summary,
                          description=summary + "\n\n本工具包将复杂流程拆解为可复现的操作步骤，包含示例数据、参数说明和常见问题排查，适合希望快速将分析方法应用到真实课题的科研工作者。",
                          category=category, level=level, price=price, author_id=creator.id, featured=featured,
                          tags=tags, cover=cover, downloads=1240 - i * 137, rating=Decimal(str(4.9 - i * 0.1)))
        db.session.add(toolkit)
        db.session.flush()
        lesson_titles = ["认识工作流与环境准备", "数据准备与质量控制", "核心分析与参数解读", "结果可视化与导出", "常见问题与进阶实践"]
        for j, lesson_title in enumerate(lesson_titles):
            db.session.add(Lesson(
                toolkit_id=toolkit.id, title=lesson_title,
                content=f"## {lesson_title}\n\n这是《{title}》的第 {j+1} 个步骤。按照本节说明运行命令并检查输出结果。\n\n### 操作要点\n\n1. 准备示例数据与运行环境\n2. 按推荐参数执行分析\n3. 检查关键质量指标并保存结果\n\n> 提示：实际研究中请根据样本类型调整参数，并保留完整运行日志。",
                sort_order=j, duration=8 + j * 4, is_preview=j == 0, image=cover))
    db.session.commit()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_data()
        prune_revoked_tokens()
    if os.getenv("JWT_SECRET_KEY", "") in ("", "dev-only-change-me", "replace-with-a-long-random-secret", "dev-local-secret-please-replace"):
        app.logger.warning("⚠️ JWT_SECRET_KEY 仍是默认/占位值，正式部署必须替换为随机密钥")
    app.run(host="0.0.0.0", port=6000, debug=os.getenv("FLASK_DEBUG", "0") == "1")
