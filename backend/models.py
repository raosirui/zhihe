"""Persistent domain models; schema creation is an explicit operator action."""
from datetime import datetime, timezone
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import column_property, deferred, object_session
from extensions import db

ASSET_PATH = "/api/uploads/"
LESSON_ASSET_PATH = "/api/lesson-asset/"


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def long_text():
    return db.Text().with_variant(LONGTEXT, "mysql")


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
    token_version = db.Column(db.Integer, default=0, server_default="0", nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    def public(self):
        return {"id": self.id, "username": self.username, "nickname": self.nickname,
                "role": self.role, "avatar": self.avatar, "bio": self.bio,
                "balance": float(self.balance or 0), "frozenBalance": float(self.frozen_balance or 0),
                "createdAt": self.created_at.isoformat()}


class Toolkit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(140), unique=True, nullable=False)
    summary = db.Column(db.String(500), nullable=False)
    description = deferred(db.Column(long_text(), nullable=False))
    category = db.Column(db.String(50), nullable=False)
    level = db.Column(db.Enum("入门", "进阶", "专业"), default="入门")
    cover = db.Column(db.String(500))
    price = db.Column(db.Numeric(10, 2), default=0, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    author = db.relationship("User", backref="toolkits", lazy="joined")
    status = db.Column(db.Enum("draft", "published", "offline"), default="published")
    downloads = db.Column(db.Integer, default=0)
    rating = db.Column(db.Numeric(3, 1), default=5.0)
    featured = db.Column(db.Boolean, default=False)
    tags = db.Column(db.String(300), default="")
    resource_name = db.Column(db.String(255))
    resource_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def serialize(self, user_id=None, detail=False, purchased_ids=None, favorited_ids=None, user=None):
        from access import can_download, can_learn, is_manager
        session = object_session(self) or db.session
        user = user or (session.get(User, user_id) if user_id else None)
        purchased = bool(user_id and (self.id in purchased_ids if purchased_ids is not None else
                         session.query(Purchase.id).filter_by(user_id=user_id, toolkit_id=self.id).first()))
        favorited = bool(user_id and (self.id in favorited_ids if favorited_ids is not None else
                         session.query(Favorite.id).filter_by(user_id=user_id, toolkit_id=self.id).first()))
        unlocked = can_learn(self, user, purchased)
        data = {"id": self.id, "title": self.title, "slug": self.slug, "summary": self.summary,
                "category": self.category, "level": self.level, "cover": self.cover, "price": float(self.price),
                "author": {"id": self.author.id, "nickname": self.author.nickname, "avatar": self.author.avatar},
                "downloads": self.downloads, "rating": float(self.rating), "featured": self.featured,
                "tags": [x for x in (self.tags or "").split(",") if x], "status": self.status,
                "purchased": purchased, "favorited": favorited, "lessonCount": self.lesson_count,
                "canLearn": unlocked, "canPreview": self.status == "published" and self.preview_count > 0,
                "canDownload": can_download(self, user, purchased),
                "canManage": is_manager(self, user), "createdAt": self.created_at.isoformat()}
        if detail:
            data.update({"description": self.description, "resourceName": self.resource_name,
                         "lessons": [x.serialize(can_learn(self, user, purchased, x))
                                     for x in sorted(self.lessons, key=lambda x: (x.sort_order, x.id))]})
        return data


class Lesson(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    toolkit_id = db.Column(db.Integer, db.ForeignKey("toolkit.id", ondelete="CASCADE"), nullable=False)
    toolkit = db.relationship("Toolkit", backref=db.backref("lessons", cascade="all, delete-orphan"))
    title = db.Column(db.String(160), nullable=False)
    content = deferred(db.Column(long_text(), nullable=False))
    image = db.Column(db.String(500))
    sort_order = db.Column(db.Integer, default=0)
    duration = db.Column(db.Integer, default=10)
    is_preview = db.Column(db.Boolean, default=False)

    def serialize(self, can_view=False):
        image = self.image if can_view else None
        if image and image.startswith("file:"):
            image = f"{LESSON_ASSET_PATH}{image[5:]}"
        return {"id": self.id, "title": self.title, "sortOrder": self.sort_order,
                "duration": self.duration, "isPreview": self.is_preview, "locked": not can_view,
                "canLearn": bool(can_view), "content": self.content if can_view else None, "image": image}


Toolkit.lesson_count = column_property(db.select(db.func.count(Lesson.id)).where(
    Lesson.toolkit_id == Toolkit.id).correlate_except(Lesson).scalar_subquery())
Toolkit.preview_count = column_property(db.select(db.func.count(Lesson.id)).where(
    Lesson.toolkit_id == Toolkit.id, Lesson.is_preview.is_(True)).correlate_except(Lesson).scalar_subquery())


class Favorite(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "toolkit_id"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    toolkit_id = db.Column(db.Integer, db.ForeignKey("toolkit.id"), nullable=False)
    toolkit = db.relationship("Toolkit")
    created_at = db.Column(db.DateTime, default=utcnow)


class Order(db.Model):
    __table_args__ = (db.Index("ix_order_buyer_toolkit_status", "buyer_id", "toolkit_id", "status"),)
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
        return {"id": self.id, "orderNo": self.order_no, "amount": float(self.amount),
                "commission": float(self.commission), "status": self.status,
                "toolkit": {"id": self.toolkit.id, "title": self.toolkit.title, "cover": self.toolkit.cover, "status": self.toolkit.status},
                "canPay": self.status == "pending" and self.toolkit.status == "published",
                "seller": self.seller.nickname, "buyer": self.buyer.nickname,
                "createdAt": self.created_at.isoformat(), "paidAt": self.paid_at.isoformat() if self.paid_at else None}


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
    event_key = db.Column(db.String(160), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    def serialize(self):
        return {"id": self.id, "type": self.type, "amount": float(self.amount),
                "balanceAfter": float(self.balance_after), "note": self.note, "createdAt": self.created_at.isoformat()}


class WalletReceipt(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "operation", "key_digest", name="uq_wallet_receipt_key"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    operation = db.Column(db.String(16), nullable=False)
    key_digest = db.Column(db.String(64), nullable=False)
    payload_digest = db.Column(db.String(64), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey("wallet_transaction.id"), nullable=False)
    response_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


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


class RateLimitGuard(db.Model):
    id = db.Column(db.Integer, primary_key=True)


class RateLimitBucket(db.Model):
    key_digest = db.Column(db.String(64), primary_key=True)
    hits = db.Column(db.Integer, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)


class FileAsset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stored_name = db.Column(db.String(233), unique=True, nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    purpose = db.Column(db.String(16), nullable=False)
    size = db.Column(db.BigInteger, nullable=False)
    sha256 = db.Column(db.String(64), nullable=True)
    state = db.Column(db.String(24), nullable=False, default="staging")
    is_public = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class FileReference(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("file_asset.id"), nullable=False, index=True)
    toolkit_id = db.Column(db.Integer, db.ForeignKey("toolkit.id"), nullable=False, index=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id"), nullable=True)
    usage = db.Column(db.String(24), nullable=False)
    binding_key = db.Column(db.String(160), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class FileOperation(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("file_asset.id"), nullable=False, index=True)
    action = db.Column(db.String(24), nullable=False)
    status = db.Column(db.String(24), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
