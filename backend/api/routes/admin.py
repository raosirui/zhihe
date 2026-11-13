"""Administrative reporting, pagination and toolkit moderation endpoints."""
from flask import jsonify, request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from api import api
from api.support import _manage_locked, _serialize_list
from extensions import db
from models import Order, Toolkit, ToolkitRating, User
from security import APIError, admin_required, current_user, json_object, positive_id, request_identity

_PER_PAGE = 20
_MAX_PER_PAGE = 100


def _paging(default=_PER_PAGE):
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", default))
    except ValueError:
        raise APIError("页码或每页数量无效") from None
    if not 1 <= page <= 100_000 or not 1 <= per_page <= _MAX_PER_PAGE:
        raise APIError("页码或每页数量无效")
    return page, per_page


@api.get("/api/admin/stats")
@admin_required
def admin_stats():
    revenue = db.session.execute(select(func.coalesce(func.sum(Order.amount), 0)).where(Order.status == "paid")).scalar_one()
    return jsonify({"users": User.query.count(), "toolkits": Toolkit.query.count(),
                    "orders": Order.query.filter_by(status="paid").count(), "revenue": float(revenue)})


@api.get("/api/admin/toolkits")
@admin_required
def admin_toolkits():
    """Paginated toolkit catalogue with status search filters (author view)."""
    query = Toolkit.query.options(selectinload(Toolkit.author)).order_by(Toolkit.created_at.desc(), Toolkit.id.desc())
    status = request.args.get("status")
    if status in {"published", "draft", "offline"}:
        query = query.filter(Toolkit.status == status)
    keyword = (request.args.get("q") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.join(User, Toolkit.author_id == User.id).filter(or_(Toolkit.title.like(like),
                    Toolkit.slug.like(like), User.nickname.like(like), User.username.like(like)))
    featured = request.args.get("featured")
    if featured in {"1", "true"}:
        query = query.filter(Toolkit.featured.is_(True))
    page, per_page = _paging()
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({"items": _serialize_list(pagination.items, current_user()), "total": pagination.total,
                    "page": pagination.page, "pages": pagination.pages})


@api.get("/api/admin/orders")
@admin_required
def admin_orders():
    """Paginated, read-only order ledger with status/keyword filters."""
    query = Order.query.options(joinedload(Order.toolkit), joinedload(Order.buyer),
                                joinedload(Order.seller)).order_by(Order.created_at.desc(), Order.id.desc())
    status = request.args.get("status")
    if status in {"pending", "paid", "cancelled", "refunded"}:
        query = query.filter(Order.status == status)
    keyword = (request.args.get("q") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.join(Toolkit, Order.toolkit_id == Toolkit.id).join(User, Order.buyer_id == User.id).filter(
            or_(Order.order_no.like(like), Toolkit.title.like(like), User.username.like(like), User.nickname.like(like)))
    page, per_page = _paging()
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({"items": [item.serialize() for item in pagination.items], "total": pagination.total,
                    "page": pagination.page, "pages": pagination.pages})


@api.get("/api/admin/users")
@admin_required
def admin_users():
    """Paginated user roster with role filter and a bounded enrichment pass."""
    query = User.query.order_by(User.id)
    role = request.args.get("role")
    if role in {"admin", "user"}:
        query = query.filter(User.role == role)
    keyword = (request.args.get("q") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(or_(User.username.like(like), User.nickname.like(like), User.email.like(like)))
    page, per_page = _paging()
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    users = pagination.items
    ids = [u.id for u in users]
    published, sales, spent, ratings = {}, {}, {}, {}
    if ids:
        published = dict(db.session.execute(select(Toolkit.author_id, func.count()).where(
            Toolkit.author_id.in_(ids), Toolkit.status == "published").group_by(Toolkit.author_id)).all())
        sales = dict(db.session.execute(select(Order.seller_id, func.coalesce(func.sum(Order.commission), 0)).where(
            Order.seller_id.in_(ids), Order.status == "paid").group_by(Order.seller_id)).all())
        spent = dict(db.session.execute(select(Order.buyer_id, func.coalesce(func.sum(Order.amount), 0)).where(
            Order.buyer_id.in_(ids), Order.status == "paid").group_by(Order.buyer_id)).all())
        ratings = dict(db.session.execute(select(ToolkitRating.user_id, func.count()).where(
            ToolkitRating.user_id.in_(ids)).group_by(ToolkitRating.user_id)).all())
    items = []
    for u in users:
        data = u.public()
        data.update(published=published.get(u.id, 0), income=float(sales.get(u.id, 0) or 0),
                    spent=float(spent.get(u.id, 0) or 0), ratings=ratings.get(u.id, 0))
        items.append(data)
    return jsonify({"items": items, "total": pagination.total, "page": pagination.page, "pages": pagination.pages})


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


@api.get("/api/admin/toolkits/<int:toolkit_id>")
@admin_required
def admin_toolkit_detail(toolkit_id):
    """Full detail for the moderation drawer: description, lessons, resources."""
    user = current_user()
    toolkit = db.session.get(Toolkit, positive_id(toolkit_id, "工具包ID"))
    if not toolkit:
        raise APIError("工具包不存在", 404)
    return jsonify(toolkit.serialize(user.id, detail=True, user=user))


@api.post("/api/admin/orders/<int:order_id>/refund")
@admin_required
def refund_order(order_id):
    # No query, financial mutation or historic data repair in this endpoint.
    raise APIError("平台不支持退款", 403, "REFUNDS_DISABLED")
