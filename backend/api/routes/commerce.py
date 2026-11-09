"""Orders, wallet, and paid resource download endpoints."""
import shutil
import tempfile
import zipfile
from pathlib import Path

from flask import Response, current_app, jsonify, request, send_from_directory
from flask_jwt_extended import jwt_required
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from api import api
from api.support import reclaim_asset, safe_stored_name
from extensions import db
from models import FileAsset, FileReference, Order, Toolkit, WalletTransaction
from security import APIError, assert_identity, request_identity, current_user, positive_id, json_object, client_ip, rate_hits
from access import can_download, is_visible
from file_service import directory, open_asset, send_asset
from document_converter import convert_document, detect_content_type, sanitize_html
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
            response = (send_asset(asset, as_attachment=True, download_name=toolkit.resource_name or "resource")
                        if asset else send_from_directory(directory("resource"), toolkit.resource_path,
                                                          as_attachment=True, download_name=toolkit.resource_name or "resource"))
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
            response = send_asset(asset, as_attachment=True,
                                  download_name=ref.name or asset.stored_name.split("_", 1)[-1])
            session.execute(update(Toolkit).where(Toolkit.id == toolkit_id).values(downloads=db.func.coalesce(Toolkit.downloads, 0) + 1))
    except Exception:
        if response:
            response.close()
        raise
    return response


_PREVIEW_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf"}


@api.get("/api/toolkits/<int:toolkit_id>/resources/<int:ref_id>/preview")
@jwt_required()
def preview_resource(toolkit_id, ref_id):
    """Never expose bucket URLs: authorize this reference before safe rendering."""
    identity = request_identity()
    rate_hits([(f"resource-preview:{identity.uid}", 30, 60),
               (f"resource-preview-ip:{client_ip()}", 120, 600)])
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        toolkit = money.lock_toolkit(session, toolkit_id)
        if not toolkit:
            raise APIError("工具包不存在", 404)
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        purchased = money.current_purchase(session, user.id, toolkit_id) is not None
        if not is_visible(toolkit, user, purchased) or not can_download(toolkit, user, purchased):
            raise APIError("购买后方可预览", 403)
        ref = session.execute(select(FileReference).where(FileReference.id == ref_id,
                              FileReference.toolkit_id == toolkit_id, FileReference.usage == "resource")).scalar_one_or_none()
        if not ref:
            raise APIError("附件不存在", 404)
        asset = ref.asset
        if (not asset or asset.purpose != "resource" or asset.state not in {"active", "legacy", "retained"}
                or not safe_stored_name(asset.stored_name)):
            raise APIError("附件不存在", 404)
        name = ref.name or asset.stored_name.split("_", 1)[-1]
        size = asset.size

    suffix = Path(name).suffix.lower()
    if suffix in _PREVIEW_MIMES:
        return send_asset(asset, mimetype=_PREVIEW_MIMES[suffix], inline_preview=True)
    kind = detect_content_type(name)
    if not kind or kind == "pdf" or size > 16 * 1024 * 1024:
        raise APIError("此类型暂不支持在线预览，请下载查看", 415)
    if kind == "markdown":
        if size > 512 * 1024:
            raise APIError("文本文件过大，请下载查看", 415)
        with open_asset(asset) as source:
            return Response(source.read().decode("utf-8", errors="replace"),
                            content_type="text/plain; charset=utf-8")
    if kind in {"html", "richtext"} and size > 1024 * 1024:
        raise APIError("此文档过大，请下载查看", 415)
    try:
        with tempfile.TemporaryDirectory(prefix="zhihe-resource-preview-") as staging:
            path = Path(staging) / ("preview" + suffix)
            with open_asset(asset) as source, path.open("wb") as target:
                shutil.copyfileobj(source, target, 128 * 1024)
            if kind in {"word", "ppt"}:
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    if (len(members) > 2048 or
                            sum(entry.file_size for entry in members) > 64 * 1024 * 1024 or
                            any(entry.file_size > 16 * 1024 * 1024 or
                                (entry.file_size > 1024 * 1024 and entry.file_size > 500 * max(entry.compress_size, 1))
                                for entry in members)):
                        raise ValueError("Archive exceeds preview budget")
            html = sanitize_html(convert_document(path, kind))
        return Response(html, content_type="text/html; charset=utf-8")
    except (ValueError, zipfile.BadZipFile, OSError):
        raise APIError("文档无法安全预览，请下载查看", 422) from None
