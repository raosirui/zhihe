"""Administrative reporting and toolkit moderation endpoints."""
from flask import jsonify
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from api import api
from api.support import _manage_locked, _serialize_list
from extensions import db
from models import Order, Toolkit, User
from security import APIError, admin_required, current_user, json_object, request_identity


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
    return jsonify([item.serialize() for item in rows])


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
