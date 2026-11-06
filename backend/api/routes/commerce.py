"""Orders, wallet, and paid resource download endpoints."""
from flask import current_app, jsonify, request, send_from_directory
from flask_jwt_extended import jwt_required
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from api import api
from api.support import reclaim_asset, safe_stored_name
from extensions import db
from models import FileAsset, FileReference, Order, Toolkit, WalletTransaction
from security import APIError, assert_identity, request_identity, current_user, positive_id, json_object
from access import can_download, is_visible
from file_service import directory
import money_service as money


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
    return jsonify([item.serialize() for item in rows])


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
                    "transactions": [item.serialize() for item in txs], "walletMode": current_app.config["WALLET_MODE"],
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


@api.get("/api/toolkits/<int:toolkit_id>/resources/<int:ref_id>/download")
@jwt_required()
def download_resource(toolkit_id, ref_id):
    """Per-attachment download from the multi-file registry; falls back to the
    legacy single resource only when the requested ref is that file."""
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
            if not is_visible(toolkit, user, purchased) or not can_download(toolkit, user, purchased):
                raise APIError("购买后方可下载", 403)
            ref = session.execute(select(FileReference).where(FileReference.id == ref_id,
                                FileReference.toolkit_id == toolkit_id, FileReference.usage == "resource")).scalar_one_or_none()
            if not ref:
                raise APIError("附件不存在", 404)
            asset = session.execute(select(FileAsset).where(FileAsset.id == ref.asset_id).with_for_update()
                                    .execution_options(populate_existing=True)).scalar_one_or_none()
            if (not asset or asset.purpose != "resource"
                    or asset.state not in {"active", "legacy", "retained"}
                    or not safe_stored_name(asset.stored_name)):
                raise APIError("附件不存在", 404)
            response = send_from_directory(directory("resource"), asset.stored_name,
                                           as_attachment=True, download_name=ref.name or asset.stored_name.split("_", 1)[-1])
            session.execute(update(Toolkit).where(Toolkit.id == toolkit_id).values(downloads=db.func.coalesce(Toolkit.downloads, 0) + 1))
    except Exception:
        if response:
            response.close()
        raise
    return response
