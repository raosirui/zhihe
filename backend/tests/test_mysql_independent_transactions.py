"""Deterministic missing-key races on the owned, fresh RR/MySQL fixture only."""
from decimal import Decimal
import re
import threading
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from test_fixtures import login_headers
from test_mysql_regression import request

pytestmark = pytest.mark.mysql


def _accounts(app, instance, count):
    from extensions import db
    from models import Purchase, RevokedToken, User, WalletReceipt, WalletTransaction
    instance.assert_owned()
    password = "OwnedGapFixture@123"
    password_hash = generate_password_hash(password)
    with app.app_context():
        for model in (Purchase, WalletReceipt, RevokedToken, WalletTransaction):
            assert db.session.query(model).count() == 0, "Expected a fresh owned fixture"
        users = [User(username=f"gap_{uuid.uuid4().hex}", nickname=f"Gap fixture {i}",
                      password_hash=password_hash, role="user", balance=Decimal("300.00"),
                      frozen_balance=Decimal("25.00"), token_version=0) for i in range(count)]
        db.session.add_all(users)
        db.session.commit()
        accounts = [{"id": user.id, "username": user.username, "password": password} for user in users]
    for account in accounts:
        with app.test_client() as client:
            account["auth"] = login_headers(client, account)
    assert len({account["id"] for account in accounts}) == count
    return accounts


def _pending(app, seller, buyer):
    from extensions import db
    from models import Toolkit
    with app.app_context():
        toolkit = Toolkit(title=f"Gap fixture {uuid.uuid4().hex}", slug=f"gap-{uuid.uuid4().hex}",
                          summary="Owned concurrency fixture", description="No files or external resources",
                          category="生信分析", price=Decimal("100.00"), author_id=seller["id"], status="published")
        db.session.add(toolkit)
        db.session.commit()
        toolkit_id = toolkit.id
    code, order = request(app, "POST", "/api/orders", buyer["auth"], {"toolkitId": toolkit_id})
    assert code == 201, (code, order)
    return toolkit_id, order["id"]


def _from(statement, table):
    return bool(re.search(rf"\bFROM\s+`?{re.escape(table)}`?\b", statement, re.I))


def _integer_parameters(parameters):
    values = parameters.values() if isinstance(parameters, dict) else parameters
    return [value for value in values if type(value) is int]


def _owned_deadlock(instance):
    instance.assert_owned()
    with instance.connect(instance.database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW ENGINE INNODB STATUS")
            status = cursor.fetchone()[2]
    begin = status.find("LATEST DETECTED DEADLOCK")
    end = status.find("TRANSACTIONS", begin + 1)
    return status[begin:end][:16000] if begin >= 0 else "No owned deadlock report"


def _at_missing_key(app, instance, operations, table):
    """Barrier after each real lookup, with disjoint stable User locks held.

    The table is initially empty. Both lookup statements have actually completed
    before either worker can proceed to insert. This works for a locking lookup
    (the pre-fix bug) and for fresh nonlocking discovery (the fixed protocol).
    Request JWT callbacks are excluded by requiring the business User lock first.
    """
    from extensions import db
    with app.app_context():
        engine = db.engine
    names = {f"qa-independent-{i}" for i in range(len(operations))}
    barrier = threading.Barrier(len(operations), timeout=12)
    user_locks, tx_connections, stages, inserts, outcomes, errors = {}, {}, {}, {}, {}, []
    guard = threading.Lock()

    def after_lookup(connection, _cursor, statement, parameters, *_args):
        name = threading.current_thread().name
        if name not in names:
            return
        if "FOR UPDATE" in statement.upper() and _from(statement, "user"):
            with guard:
                user_locks.setdefault(name, []).extend(_integer_parameters(parameters))
                tx_connections.setdefault(name, connection.connection.driver_connection.thread_id())
        if (statement.lstrip().upper().startswith("SELECT") and _from(statement, table)
                and name in user_locks and name not in stages):
            with guard:
                stages[name] = {"locked": "FOR UPDATE" in statement.upper(),
                                "parameters": tuple(parameters.values()) if isinstance(parameters, dict) else tuple(parameters)}
            barrier.wait()

    def before_insert(_connection, _cursor, statement, _parameters, *_args):
        name = threading.current_thread().name
        match = re.match(r"\s*INSERT\s+INTO\s+`?(\w+)", statement, re.I)
        if name in names and match:
            with guard:
                inserts.setdefault(name, []).append(match.group(1))

    def handle_error(context):
        original = context.original_exception
        if original.args and original.args[0] in (1205, 1213):
            with guard:
                errors.append((threading.current_thread().name, original.args[0], context.statement))

    def worker(name, operation):
        try:
            outcomes[name] = request(app, *operation)
        except BaseException as exc:
            outcomes[name] = ("worker-error", type(exc).__name__, str(exc))

    event.listen(engine, "after_cursor_execute", after_lookup)
    event.listen(engine, "before_cursor_execute", before_insert)
    event.listen(engine, "handle_error", handle_error)
    threads = [threading.Thread(name=f"qa-independent-{i}", target=worker,
                                args=(f"qa-independent-{i}", operation), daemon=True)
               for i, operation in enumerate(operations)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        barrier.abort()
        for thread in threads:
            thread.join(timeout=15)
        event.remove(engine, "after_cursor_execute", after_lookup)
        event.remove(engine, "before_cursor_execute", before_insert)
        event.remove(engine, "handle_error", handle_error)
    assert not any(thread.is_alive() for thread in threads), "Owned request worker did not finish"
    diagnostics = f"outcomes={outcomes}; raw_errors={errors}; stages={stages}; writes={inserts}"
    if errors or any(result[0] != 200 for result in outcomes.values()):
        graph = _owned_deadlock(instance) if any(error[1] == 1213 for error in errors) else "No current 1213 captured"
        pytest.fail(f"{diagnostics}\n{graph}")
    assert set(stages) == names, diagnostics
    assert set(user_locks) == names, diagnostics
    assert len(set(tx_connections.values())) == len(operations), "Business transactions shared a connection"
    locked_sets = [set(user_locks[name]) for name in sorted(names)]
    for i, left in enumerate(locked_sets):
        for right in locked_sets[i + 1:]:
            assert left.isdisjoint(right), f"Shared User locks would hide the gap race: {user_locks}"
    # No random scheduling guess: every worker reached the missing key and its
    # business INSERT phase, with the barrier inside the actual SQL execution.
    assert set(inserts) == names, diagnostics
    instance.assert_owned()
    return [outcomes[f"qa-independent-{i}"] for i in range(len(operations))]


def test_disjoint_payments_empty_purchase_keys(mysql_app, mysql_data, mysql_instance):
    from extensions import db
    from models import Order, Purchase, User, WalletTransaction
    seller_a, buyer_a, seller_b, buyer_b = _accounts(mysql_app, mysql_instance, 4)
    kit_a, order_a = _pending(mysql_app, seller_a, buyer_a)
    kit_b, order_b = _pending(mysql_app, seller_b, buyer_b)
    assert kit_a != kit_b
    _at_missing_key(mysql_app, mysql_instance, [
        ("POST", f"/api/orders/{order_a}/pay", buyer_a["auth"]),
        ("POST", f"/api/orders/{order_b}/pay", buyer_b["auth"]),
    ], "purchase")
    with mysql_app.app_context():
        assert {row.status for row in db.session.query(Order)} == {"paid"}
        assert {(row.user_id, row.toolkit_id, row.order_id) for row in db.session.query(Purchase)} == {
            (buyer_a["id"], kit_a, order_a), (buyer_b["id"], kit_b, order_b)}
        rows = db.session.query(WalletTransaction).all()
        assert len(rows) == 4 and len({row.event_key for row in rows}) == 4
        for seller, buyer in ((seller_a, buyer_a), (seller_b, buyer_b)):
            assert db.session.get(User, buyer["id"]).balance == Decimal("200.00")
            assert db.session.get(User, seller["id"]).balance == Decimal("320.00")
            assert db.session.get(User, buyer["id"]).frozen_balance == Decimal("25.00")
    # Both paid retries are read-only, preserving the exact ledger and rights.
    for order_id, buyer in ((order_a, buyer_a), (order_b, buyer_b)):
        assert request(mysql_app, "POST", f"/api/orders/{order_id}/pay", buyer["auth"])[0] == 200
    with mysql_app.app_context():
        assert db.session.query(WalletTransaction).count() == 4
        assert db.session.query(Purchase).count() == 2


@pytest.mark.parametrize("operations", [("withdraw", "withdraw"), ("recharge", "recharge"), ("withdraw", "recharge")])
def test_disjoint_wallet_empty_receipt_keys(mysql_app, mysql_data, mysql_instance, operations):
    from extensions import db
    from models import User, WalletReceipt, WalletTransaction
    accounts = _accounts(mysql_app, mysql_instance, 2)
    keys = [f"owned-key-{uuid.uuid4().hex}" for _ in accounts]
    calls = [("POST", f"/api/wallet/{operation}", account["auth"], {"amount": "40.00"}, key)
             for account, operation, key in zip(accounts, operations, keys)]
    results = _at_missing_key(mysql_app, mysql_instance, calls, "wallet_receipt")
    with mysql_app.app_context():
        assert db.session.query(WalletReceipt).count() == 2
        assert db.session.query(WalletTransaction).count() == 2
        for account, operation in zip(accounts, operations):
            user = db.session.get(User, account["id"])
            assert user.balance == Decimal("340.00" if operation == "recharge" else "260.00")
            assert user.frozen_balance == Decimal("25.00")
        assert len({row.event_key for row in db.session.query(WalletTransaction)}) == 2
    # Existing receipts must return the original response; conflicts do not write.
    for call, original in zip(calls, results):
        assert request(mysql_app, *call) == original
        conflict = (*call[:3], {"amount": "41.00"}, call[4])
        assert request(mysql_app, *conflict)[0] == 409
    with mysql_app.app_context():
        assert db.session.query(WalletReceipt).count() == 2
        assert db.session.query(WalletTransaction).count() == 2


def test_disjoint_logouts_and_financial_identity_empty_revocations(mysql_app, mysql_data, mysql_instance):
    from extensions import db
    from models import RevokedToken, User, WalletTransaction
    logout_a, logout_b, wallet_user = _accounts(mysql_app, mysql_instance, 3)
    _at_missing_key(mysql_app, mysql_instance, [
        ("POST", "/api/auth/logout", logout_a["auth"]),
        ("POST", "/api/auth/logout", logout_b["auth"]),
        ("POST", "/api/wallet/recharge", wallet_user["auth"], {"amount": "40.00"}, "owned-revocation-race"),
    ], "revoked_token")
    with mysql_app.app_context():
        assert {row.user_id for row in db.session.query(RevokedToken)} == {logout_a["id"], logout_b["id"]}
        assert db.session.get(User, wallet_user["id"]).balance == Decimal("340.00")
        assert db.session.query(WalletTransaction).count() == 1
    for account in (logout_a, logout_b):
        assert request(mysql_app, "GET", "/api/auth/me", account["auth"])[0] == 401
        assert request(mysql_app, "POST", "/api/wallet/recharge", account["auth"], {"amount": "40.00"}, "revoked")[0] == 401
    assert request(mysql_app, "GET", "/api/auth/me", wallet_user["auth"])[0] == 200


def test_disjoint_logout_and_wallet_identity(mysql_app, mysql_data, mysql_instance):
    from extensions import db
    from models import RevokedToken, User, WalletTransaction
    logout_user, wallet_user = _accounts(mysql_app, mysql_instance, 2)
    _at_missing_key(mysql_app, mysql_instance, [
        ("POST", "/api/auth/logout", logout_user["auth"]),
        ("POST", "/api/wallet/withdraw", wallet_user["auth"], {"amount": "40.00"}, "owned-single-logout"),
    ], "revoked_token")
    with mysql_app.app_context():
        assert db.session.query(RevokedToken).count() == 1
        assert db.session.query(WalletTransaction).count() == 1
        assert db.session.get(User, wallet_user["id"]).balance == Decimal("260.00")


def test_financial_identity_sees_revocation_after_earlier_rr_snapshot(mysql_app, mysql_data, mysql_instance):
    """A nonlocking lookup in the money transaction must not reuse its old snapshot."""
    from extensions import db
    from flask_jwt_extended import decode_token
    from models import Order, Purchase, RevokedToken, User, WalletTransaction
    from money_service import lock_users
    seller, buyer = _accounts(mysql_app, mysql_instance, 2)
    _kit_id, order_id = _pending(mysql_app, seller, buyer)
    snapshot_ready, revoked_committed = threading.Event(), threading.Event()
    outcome = {}
    with mysql_app.app_context():
        engine = db.engine
        claims = decode_token(buyer["auth"]["Authorization"].split(" ", 1)[1])
    from datetime import datetime, timezone
    expiry = datetime.fromtimestamp(claims["exp"], timezone.utc).replace(tzinfo=None)

    def after_discovery(_connection, _cursor, statement, _parameters, *_args):
        if (threading.current_thread().name == "qa-old-identity-snapshot" and _from(statement, "order")
                and "FOR UPDATE" not in statement.upper() and statement.lstrip().startswith("SELECT `order`.toolkit_id")):
            snapshot_ready.set()
            assert revoked_committed.wait(12), "Owned revocation was not committed"

    event.listen(engine, "after_cursor_execute", after_discovery)
    payer = threading.Thread(name="qa-old-identity-snapshot", target=lambda: outcome.update(
        result=request(mysql_app, "POST", f"/api/orders/{order_id}/pay", buyer["auth"])), daemon=True)
    payer.start()
    try:
        assert snapshot_ready.wait(8), "The money transaction's RR snapshot was not established"
        mysql_instance.assert_owned()
        with mysql_app.app_context(), Session(engine) as session, session.begin():
            assert lock_users(session, [buyer["id"]])[buyer["id"]] is not None
            session.add(RevokedToken(jti=claims["jti"], user_id=buyer["id"], expires_at=expiry))
    finally:
        revoked_committed.set()
        payer.join(timeout=20)
        event.remove(engine, "after_cursor_execute", after_discovery)
    assert not payer.is_alive()
    assert outcome["result"][0] == 401, outcome
    with mysql_app.app_context():
        assert db.session.get(Order, order_id).status == "pending"
        assert db.session.get(User, buyer["id"]).balance == Decimal("300.00")
        assert db.session.query(Purchase).count() == 0
        assert db.session.query(WalletTransaction).count() == 0
    mysql_instance.assert_owned()
