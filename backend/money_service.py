"""Money transactions, isolated from the request session/identity map.

All mutations lock Toolkit -> Orders by ID -> Users by ID -> file registry.
Wallet operations lock only User. Lock queries retain refreshed ORM objects.
"""
import hashlib
import json
import uuid
from decimal import Decimal
from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload
from extensions import db
from models import Toolkit, Order, User, Purchase, WalletTransaction, WalletReceipt, utcnow
from security import APIError, assert_identity, lock_existing_child, text
from validation import parse_money

COMMISSION_RATE = Decimal("0.20")
MAX_BALANCE = Decimal("9999999999.99")


def lock_toolkit(session, toolkit_id):
    # Toolkit.author is eagerly loaded for lists, but must NOT become an
    # implicit User lock before Orders when using SELECT FOR UPDATE.
    return session.execute(select(Toolkit).options(lazyload(Toolkit.author)).where(Toolkit.id == toolkit_id)
                           .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()


def lock_orders(session, toolkit_id, buyer_id=None):
    # Caller already holds this Toolkit's stable parent lock, and has not yet
    # written orders. A fresh nonlocking read sees every committed child ID,
    # even if the money transaction's RR snapshot predates the parent wait.
    # Locking the nonunique toolkit_id range here would next-key lock gaps for
    # OTHER toolkits, creating an Order-gap -> User -> INSERT Order lock cycle.
    query = select(Order.id).where(Order.toolkit_id == toolkit_id)
    if buyer_id is not None:
        query = query.where(Order.buyer_id == buyer_id)
    with Session(db.engine) as discovery:
        ids = list(discovery.execute(query.order_by(Order.id)).scalars())
    orders = []
    for order_id in ids:
        order = session.execute(select(Order).where(Order.id == order_id).with_for_update()
                                .execution_options(populate_existing=True)).scalar_one_or_none()
        if order and order.toolkit_id == toolkit_id and (buyer_id is None or order.buyer_id == buyer_id):
            orders.append(order)
    return orders


def lock_users(session, uids):
    users = {}
    for uid in sorted(set(uids)):
        users[uid] = session.execute(select(User).where(User.id == uid).with_for_update()
                                     .execution_options(populate_existing=True)).scalar_one_or_none()
    return users


def current_purchase(session, uid, toolkit_id):
    # Toolkit/User are already locked; all purchase creation uses those parents.
    return lock_existing_child(session, Purchase, Purchase.id,
                               Purchase.user_id == uid, Purchase.toolkit_id == toolkit_id)


def create_order(identity, toolkit_id):
    if current_app.config["WALLET_MODE"] != "simulation":
        raise APIError("模拟交易未开启；尚未接入第三方支付", 403, "SIMULATION_DISABLED")
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        toolkit = lock_toolkit(session, toolkit_id)
        if not toolkit:
            raise APIError("工具包不可购买", 404)
        orders = lock_orders(session, toolkit_id, identity.uid)
        # Order.seller_id's FK would otherwise take an implicit seller lock
        # after the buyer, reversing User ID order against another toolkit pay.
        users = lock_users(session, [identity.uid, toolkit.author_id])
        user = users[identity.uid]
        assert_identity(session, user, identity)
        if users[toolkit.author_id] is None:
            raise APIError("工具包作者不存在", 409)
        if toolkit.status != "published":
            raise APIError("工具包不可购买", 404)
        if toolkit.author_id == user.id:
            raise APIError("不能购买自己发布的工具包")
        if toolkit.price <= 0:
            raise APIError("免费工具包无需购买")
        if current_purchase(session, user.id, toolkit_id):
            raise APIError("你已购买此工具包", 409, "ALREADY_PURCHASED")
        existing = next((x for x in orders if x.status == "pending"), None)
        if existing:
            result, status = existing.serialize(), 200
        else:
            amount = parse_money(toolkit.price, Decimal("0.01"), Decimal("9999"))
            order = Order(order_no=f"ZH{utcnow():%Y%m%d%H%M%S}{uuid.uuid4().hex[:12].upper()}",
                          buyer_id=user.id, seller_id=toolkit.author_id, toolkit_id=toolkit_id,
                          amount=amount, commission=parse_money(amount * COMMISSION_RATE, 0, 9999), status="pending")
            session.add(order)
            session.flush()
            result, status = order.serialize(), 201
    return result, status


def _locked_order_context(session, order_id, identity):
    # This discovery read acquires no locks; identifiers never change. The
    # current-read Toolkit and Order objects below define the transaction.
    toolkit_id = session.execute(select(Order.toolkit_id).where(Order.id == order_id)).scalar_one_or_none()
    if toolkit_id is None:
        raise APIError("订单不存在", 404)
    toolkit = lock_toolkit(session, toolkit_id)
    if not toolkit:
        raise APIError("订单工具包不存在", 404)
    # Lock every related order in ID order, including historical duplicate
    # pending orders. This also prevents a pay/cancel lock-order inversion.
    orders = lock_orders(session, toolkit_id)
    order = next((x for x in orders if x.id == order_id), None)
    if not order:
        raise APIError("订单不存在", 404)
    users = lock_users(session, [identity.uid, order.buyer_id, order.seller_id])
    assert_identity(session, users[identity.uid], identity)
    if order.buyer_id != identity.uid:
        raise APIError("无权操作他人订单", 403)
    return toolkit, orders, order, users


def pay_order(identity, order_id):
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        toolkit, orders, order, users = _locked_order_context(session, order_id, identity)
        buyer, seller = users[order.buyer_id], users[order.seller_id]
        if order.status == "paid":
            # Do not rebuild financial history or missing historic Purchases.
            result, status = {"order": order.serialize(), "user": buyer.public(), "idempotent": True}, 200
        elif order.status != "pending":
            raise APIError("订单状态不可支付", 409, "ORDER_NOT_PENDING")
        elif current_app.config["WALLET_MODE"] != "simulation":
            raise APIError("模拟交易未开启；尚未接入第三方支付", 403, "SIMULATION_DISABLED")
        elif current_purchase(session, buyer.id, toolkit.id):
            for sibling in orders:
                if sibling.buyer_id == buyer.id and sibling.status == "pending":
                    sibling.status = "cancelled"
            session.flush()
            result, status = {"order": order.serialize(), "user": buyer.public(), "code": "ALREADY_PURCHASED",
                              "message": "已拥有此工具包，重复待付款订单已取消"}, 409
        else:
            if toolkit.status != "published":
                raise APIError("工具包已下架，不能支付，请取消订单", 409, "TOOLKIT_NOT_FOR_SALE")
            amount = parse_money(order.amount, Decimal("0.01"), Decimal("9999"))
            commission = parse_money(order.commission, 0, amount)
            if not seller or seller.id == buyer.id:
                raise APIError("订单收款方无效", 409)
            if buyer.balance < amount:
                raise APIError("余额不足，请先充值")
            if seller.balance + commission > MAX_BALANCE:
                raise APIError("收款方余额超过限额", 409)
            buyer.balance -= amount
            seller.balance += commission
            order.status, order.paid_at = "paid", utcnow()
            session.add(Purchase(user_id=buyer.id, toolkit_id=toolkit.id, order_id=order.id))
            session.add(WalletTransaction(user_id=buyer.id, type="purchase", amount=-amount,
                        balance_after=buyer.balance, note=f"购买《{toolkit.title}》", order_id=order.id,
                        event_key=f"order:{order.id}:purchase"))
            session.add(WalletTransaction(user_id=seller.id, type="commission", amount=commission,
                        balance_after=seller.balance, note=f"《{toolkit.title}》销售分成", order_id=order.id,
                        event_key=f"order:{order.id}:commission"))
            # Historical duplicate pending rows become explicit cancellations,
            # not a second debit and not an integrity-error 500.
            for sibling in orders:
                if sibling.id != order.id and sibling.buyer_id == buyer.id and sibling.status == "pending":
                    sibling.status = "cancelled"
            session.flush()
            result, status = {"order": order.serialize(), "user": buyer.public()}, 200
    return result, status


def cancel_order(identity, order_id):
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        _, _, order, _ = _locked_order_context(session, order_id, identity)
        if order.status == "pending":
            order.status = "cancelled"
        elif order.status != "cancelled":
            raise APIError("订单不可取消", 409, "ORDER_NOT_PENDING")
        session.flush()
        result = order.serialize()
    return result


def wallet_operation(identity, operation, amount, key):
    if current_app.config["WALLET_MODE"] != "simulation":
        raise APIError("模拟资金功能未开启；尚未接入第三方支付", 403, "SIMULATION_DISABLED")
    if key is None:
        raise APIError("请提供 Idempotency-Key 请求头", 400, "IDEMPOTENCY_KEY_REQUIRED")
    key = text(key, "Idempotency-Key", 128, 1)
    if not all(33 <= ord(c) <= 126 for c in key):
        raise APIError("Idempotency-Key 须为可打印 ASCII 字符")
    minimum = Decimal("0.01") if operation == "recharge" else Decimal("10")
    amount = parse_money(amount, minimum, Decimal("50000"))
    # Receipts must survive JWT/rate-limit secret rotation. Client keys are
    # opaque and hashed, but their persistent identity cannot use a rotating key.
    digest = hashlib.sha256(key.encode("ascii")).hexdigest()
    payload = hashlib.sha256(f"{operation}:{amount:.2f}".encode()).hexdigest()
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        user = lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        receipt = lock_existing_child(session, WalletReceipt, WalletReceipt.id,
                                      WalletReceipt.user_id == user.id,
                                      WalletReceipt.operation == operation, WalletReceipt.key_digest == digest)
        if receipt:
            if receipt.payload_digest != payload:
                raise APIError("同一 Idempotency-Key 不能用于不同金额", 409, "IDEMPOTENCY_CONFLICT")
            result = json.loads(receipt.response_json)
        else:
            delta = amount if operation == "recharge" else -amount
            if user.balance + delta < 0:
                raise APIError("提现不能超过可用余额")
            if user.balance + delta > MAX_BALANCE:
                raise APIError("余额超过账户限额", 409)
            user.balance += delta
            tx = WalletTransaction(user_id=user.id, type="recharge" if delta > 0 else "withdrawal", amount=delta,
                    balance_after=user.balance, note="钱包充值（模拟资金，非第三方支付）" if delta > 0 else "余额提现（模拟资金，非真实到账）",
                    event_key=f"wallet:{user.id}:{operation}:{digest}")
            session.add(tx)
            session.flush()
            result = {**user.public(), "walletMode": "simulation"}
            session.add(WalletReceipt(user_id=user.id, operation=operation, key_digest=digest,
                    payload_digest=payload, amount=amount, transaction_id=tx.id,
                    response_json=json.dumps(result, ensure_ascii=False)))
    return result
