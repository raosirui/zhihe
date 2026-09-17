import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from urllib.parse import quote_plus

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, get_jwt_identity, jwt_required
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

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
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=7)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

CORS(app, origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:4000")], supports_credentials=True)
db = SQLAlchemy(app)
jwt = JWTManager(app)


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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    description = db.Column(db.Text, nullable=False)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def serialize(self, user_id=None, detail=False):
        purchased = False
        favorited = False
        if user_id:
            purchased = self.price == 0 or self.author_id == user_id or Purchase.query.filter_by(user_id=user_id, toolkit_id=self.id).first() is not None
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
    content = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(500))
    sort_order = db.Column(db.Integer, default=0)
    duration = db.Column(db.Integer, default=10)
    is_preview = db.Column(db.Boolean, default=False)

    def serialize(self, unlocked=False):
        can_view = unlocked or self.is_preview
        return {
            "id": self.id, "title": self.title, "sortOrder": self.sort_order,
            "duration": self.duration, "isPreview": self.is_preview, "locked": not can_view,
            "content": self.content if can_view else None, "image": self.image if can_view else None,
        }


class Favorite(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "toolkit_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    toolkit_id = db.Column(db.Integer, db.ForeignKey("toolkit.id"), nullable=False)
    toolkit = db.relationship("Toolkit")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def serialize(self):
        return {
            "id": self.id, "orderNo": self.order_no, "amount": float(self.amount),
            "commission": float(self.commission), "status": self.status,
            "toolkit": {"id": self.toolkit.id, "title": self.toolkit.title, "cover": self.toolkit.cover},
            "seller": self.seller.nickname, "createdAt": self.created_at.isoformat(),
            "paidAt": self.paid_at.isoformat() if self.paid_at else None,
        }


class Purchase(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "toolkit_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    toolkit_id = db.Column(db.Integer, db.ForeignKey("toolkit.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class WalletTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    type = db.Column(db.Enum("recharge", "purchase", "commission", "withdrawal"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    balance_after = db.Column(db.Numeric(12, 2), nullable=False)
    note = db.Column(db.String(255))
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def serialize(self):
        return {"id": self.id, "type": self.type, "amount": float(self.amount), "balanceAfter": float(self.balance_after), "note": self.note, "createdAt": self.created_at.isoformat()}


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


@app.errorhandler(413)
def too_large(_):
    return jsonify({"message": "文件不能超过 100MB"}), 413


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "zhihe-api"})


@app.post("/api/auth/login")
def login():
    data = request.get_json() or {}
    user = User.query.filter_by(username=data.get("username", "").strip()).first()
    if not user or not check_password_hash(user.password_hash, data.get("password", "")):
        return jsonify({"message": "账号或密码不正确"}), 401
    token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return jsonify({"token": token, "user": user.public()})


@app.post("/api/auth/register")
def register():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    if len(username) < 3 or len(data.get("password", "")) < 6:
        return jsonify({"message": "账号至少 3 位，密码至少 6 位"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"message": "账号已存在"}), 409
    user = User(username=username, nickname=data.get("nickname") or username, password_hash=generate_password_hash(data["password"]), role="user")
    db.session.add(user)
    db.session.commit()
    token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return jsonify({"token": token, "user": user.public()}), 201


@app.get("/api/auth/me")
@jwt_required()
def me():
    return jsonify(current_user().public())


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
    if price == "free": query = query.filter(Toolkit.price == 0)
    if price == "paid": query = query.filter(Toolkit.price > 0)
    sort = request.args.get("sort", "recommended")
    if sort == "newest": query = query.order_by(Toolkit.created_at.desc())
    elif sort == "popular": query = query.order_by(Toolkit.downloads.desc())
    elif sort == "price": query = query.order_by(Toolkit.price.asc())
    else: query = query.order_by(Toolkit.featured.desc(), Toolkit.downloads.desc())
    items = query.all()
    return jsonify({"items": [x.serialize(user_id) for x in items], "total": len(items)})


@app.get("/api/toolkits/<int:toolkit_id>")
def get_toolkit(toolkit_id):
    toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit or toolkit.status == "offline": return jsonify({"message": "工具包不存在"}), 404
    return jsonify(toolkit.serialize(optional_user_id(), detail=True))


@app.post("/api/toolkits")
@jwt_required()
def create_toolkit():
    user = current_user()
    data = request.form
    title = data.get("title", "").strip()
    if not title or not data.get("description"):
        return jsonify({"message": "标题和介绍不能为空"}), 400
    slug = f"{uuid.uuid4().hex[:8]}-{secure_filename(title)[:50] or 'toolkit'}"
    toolkit = Toolkit(
        title=title, slug=slug, summary=data.get("summary", ""), description=data.get("description", ""),
        category=data.get("category", "生信分析"), level=data.get("level", "入门"),
        price=Decimal(data.get("price", "0")), author_id=user.id, tags=data.get("tags", ""),
        cover=data.get("cover") or None, status="published",
    )
    resource = request.files.get("resource")
    if resource:
        filename = f"{uuid.uuid4().hex}_{secure_filename(resource.filename)}"
        resource.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        toolkit.resource_name, toolkit.resource_path = resource.filename, filename
    db.session.add(toolkit)
    db.session.flush()
    lessons = request.get_json(silent=True) or []
    if not lessons and data.get("lessons"):
        import json
        try: lessons = json.loads(data.get("lessons", "[]"))
        except ValueError: lessons = []
    for index, lesson in enumerate(lessons):
        db.session.add(Lesson(toolkit_id=toolkit.id, title=lesson.get("title", f"步骤 {index + 1}"), content=lesson.get("content", ""), image=lesson.get("image"), duration=int(lesson.get("duration", 10)), sort_order=index, is_preview=index == 0))
    db.session.commit()
    return jsonify(toolkit.serialize(user.id, detail=True)), 201


@app.put("/api/toolkits/<int:toolkit_id>/status")
@admin_required
def update_status(toolkit_id):
    toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit: return jsonify({"message": "工具包不存在"}), 404
    status = (request.get_json() or {}).get("status")
    if status not in ["published", "offline"]: return jsonify({"message": "状态无效"}), 400
    toolkit.status = status
    db.session.commit()
    return jsonify(toolkit.serialize(current_user().id))


@app.post("/api/toolkits/<int:toolkit_id>/favorite")
@jwt_required()
def toggle_favorite(toolkit_id):
    user = current_user()
    toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit: return jsonify({"message": "工具包不存在"}), 404
    favorite = Favorite.query.filter_by(user_id=user.id, toolkit_id=toolkit_id).first()
    if favorite:
        db.session.delete(favorite); active = False
    else:
        db.session.add(Favorite(user_id=user.id, toolkit_id=toolkit_id)); active = True
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


@app.post("/api/orders")
@jwt_required()
def create_order():
    user = current_user()
    toolkit_id = (request.get_json() or {}).get("toolkitId")
    toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit or toolkit.status != "published": return jsonify({"message": "工具包不可购买"}), 404
    if toolkit.author_id == user.id: return jsonify({"message": "不能购买自己发布的工具包"}), 400
    if toolkit.price == 0: return jsonify({"message": "免费工具包无需购买"}), 400
    if Purchase.query.filter_by(user_id=user.id, toolkit_id=toolkit.id).first(): return jsonify({"message": "你已购买此工具包"}), 409
    existing = Order.query.filter_by(buyer_id=user.id, toolkit_id=toolkit.id, status="pending").first()
    if existing: return jsonify(existing.serialize())
    order = Order(order_no=f"ZH{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}", buyer_id=user.id, seller_id=toolkit.author_id, toolkit_id=toolkit.id, amount=toolkit.price, commission=toolkit.price * Decimal("0.20"))
    db.session.add(order); db.session.commit()
    return jsonify(order.serialize()), 201


@app.post("/api/orders/<int:order_id>/pay")
@jwt_required()
def pay_order(order_id):
    user = current_user()
    order = Order.query.filter_by(id=order_id, buyer_id=user.id).with_for_update().first()
    if not order: return jsonify({"message": "订单不存在"}), 404
    if order.status != "pending": return jsonify({"message": "订单状态不可支付"}), 400
    if user.balance < order.amount: return jsonify({"message": "余额不足，请先充值"}), 400
    seller = db.session.get(User, order.seller_id)
    user.balance -= order.amount
    seller.balance += order.commission
    order.status, order.paid_at = "paid", datetime.utcnow()
    db.session.add(Purchase(user_id=user.id, toolkit_id=order.toolkit_id, order_id=order.id))
    db.session.add(WalletTransaction(user_id=user.id, type="purchase", amount=-order.amount, balance_after=user.balance, note=f"购买《{order.toolkit.title}》", order_id=order.id))
    db.session.add(WalletTransaction(user_id=seller.id, type="commission", amount=order.commission, balance_after=seller.balance, note=f"《{order.toolkit.title}》销售分成", order_id=order.id))
    db.session.commit()
    return jsonify({"order": order.serialize(), "user": user.public()})


@app.post("/api/orders/<int:order_id>/cancel")
@jwt_required()
def cancel_order(order_id):
    order = Order.query.filter_by(id=order_id, buyer_id=current_user().id).first()
    if not order or order.status != "pending": return jsonify({"message": "订单不可取消"}), 400
    order.status = "cancelled"; db.session.commit()
    return jsonify(order.serialize())


@app.get("/api/me/orders")
@jwt_required()
def my_orders():
    rows = Order.query.filter_by(buyer_id=current_user().id).order_by(Order.created_at.desc()).all()
    return jsonify([x.serialize() for x in rows])


@app.get("/api/wallet")
@jwt_required()
def wallet():
    user = current_user()
    txs = WalletTransaction.query.filter_by(user_id=user.id).order_by(WalletTransaction.created_at.desc()).limit(100).all()
    income = db.session.query(db.func.sum(WalletTransaction.amount)).filter_by(user_id=user.id, type="commission").scalar() or 0
    return jsonify({"balance": float(user.balance), "frozenBalance": float(user.frozen_balance), "totalIncome": float(income), "transactions": [x.serialize() for x in txs]})


@app.post("/api/wallet/recharge")
@jwt_required()
def recharge():
    user = current_user()
    try: amount = Decimal(str((request.get_json() or {}).get("amount", 0)))
    except Exception: return jsonify({"message": "金额无效"}), 400
    if amount <= 0 or amount > 50000: return jsonify({"message": "单次充值金额须在 0.01～50000 元"}), 400
    user.balance += amount
    db.session.add(WalletTransaction(user_id=user.id, type="recharge", amount=amount, balance_after=user.balance, note="钱包充值（模拟支付）"))
    db.session.commit()
    return jsonify(user.public())


@app.post("/api/wallet/withdraw")
@jwt_required()
def withdraw():
    user = current_user()
    try: amount = Decimal(str((request.get_json() or {}).get("amount", 0)))
    except Exception: return jsonify({"message": "金额无效"}), 400
    if amount < 10 or amount > user.balance: return jsonify({"message": "提现至少 10 元且不能超过可用余额"}), 400
    user.balance -= amount
    db.session.add(WalletTransaction(user_id=user.id, type="withdrawal", amount=-amount, balance_after=user.balance, note="余额提现（模拟到账）"))
    db.session.commit()
    return jsonify(user.public())


@app.get("/api/toolkits/<int:toolkit_id>/download")
@jwt_required()
def download(toolkit_id):
    user = current_user(); toolkit = db.session.get(Toolkit, toolkit_id)
    if not toolkit: return jsonify({"message": "工具包不存在"}), 404
    unlocked = toolkit.price == 0 or toolkit.author_id == user.id or Purchase.query.filter_by(user_id=user.id, toolkit_id=toolkit.id).first()
    if not unlocked: return jsonify({"message": "购买后方可下载"}), 403
    if not toolkit.resource_path: return jsonify({"message": "作者暂未上传资源"}), 404
    toolkit.downloads += 1; db.session.commit()
    return send_from_directory(app.config["UPLOAD_FOLDER"], toolkit.resource_path, as_attachment=True, download_name=toolkit.resource_name)


@app.get("/api/admin/stats")
@admin_required
def admin_stats():
    revenue = db.session.query(db.func.sum(Order.amount)).filter_by(status="paid").scalar() or 0
    return jsonify({"users": User.query.count(), "toolkits": Toolkit.query.count(), "orders": Order.query.filter_by(status="paid").count(), "revenue": float(revenue)})


def seed_data():
    if User.query.count(): return
    admin = User(username="admin", nickname="知核管理员", password_hash=generate_password_hash("Admin@123"), role="admin", balance=Decimal("1000"), bio="维护知识的秩序")
    researcher = User(username="researcher", nickname="林博士", password_hash=generate_password_hash("User@123"), role="user", balance=Decimal("288"), bio="单细胞转录组与肿瘤微环境")
    creator = User(username="creator", nickname="BioLab·周", password_hash=generate_password_hash("Creator@123"), role="user", balance=Decimal("1268.50"), bio="生物信息学工具作者")
    db.session.add_all([admin, researcher, creator]); db.session.flush()
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
        toolkit = Toolkit(title=title, slug=f"demo-{i+1}", summary=summary, description=summary + "\n\n本工具包将复杂流程拆解为可复现的操作步骤，包含示例数据、参数说明和常见问题排查，适合希望快速将分析方法应用到真实课题的科研工作者。", category=category, level=level, price=price, author_id=creator.id, featured=featured, tags=tags, cover=cover, downloads=1240-i*137, rating=Decimal(str(4.9-i*0.1)))
        db.session.add(toolkit); db.session.flush()
        lesson_titles = ["认识工作流与环境准备", "数据准备与质量控制", "核心分析与参数解读", "结果可视化与导出", "常见问题与进阶实践"]
        for j, lesson_title in enumerate(lesson_titles):
            db.session.add(Lesson(toolkit_id=toolkit.id, title=lesson_title, content=f"## {lesson_title}\n\n这是《{title}》的第 {j+1} 个步骤。按照本节说明运行命令并检查输出结果。\n\n### 操作要点\n\n1. 准备示例数据与运行环境\n2. 按推荐参数执行分析\n3. 检查关键质量指标并保存结果\n\n> 提示：实际研究中请根据样本类型调整参数，并保留完整运行日志。", sort_order=j, duration=8+j*4, is_preview=j == 0, image=cover))
    db.session.commit()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_data()
    app.run(host="0.0.0.0", port=6000, debug=True)
