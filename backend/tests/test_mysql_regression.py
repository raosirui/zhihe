"""Real RR transactions, independent request connections, and financial invariants."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import re
import threading
import uuid

import pytest
from sqlalchemy import event

from test_fixtures import login_headers

pytestmark = pytest.mark.mysql


def headers(app, data, role="buyer"):
    with app.test_client() as client:
        return login_headers(client, data["users"][role])


def request(app, method, path, auth, body=None, key=None):
    with app.test_client() as client:
        response = client.open(path, method=method, json=body,
                               headers={**auth, **({"Idempotency-Key": key} if key else {})})
        return response.status_code, response.get_json()


def create_order(app, data, auth, toolkit=None):
    code, order = request(app, "POST", "/api/orders", auth,
                          {"toolkitId": toolkit or data["toolkits"]["paid"]})
    assert code in (200, 201), order
    assert isinstance(order.get("id"), int), order
    return order


def race(app, operations):
    barrier = threading.Barrier(len(operations), timeout=10)

    def run(operation):
        barrier.wait()
        return request(app, *operation)

    with ThreadPoolExecutor(max_workers=len(operations), thread_name_prefix="qa-race") as pool:
        futures = [pool.submit(run, operation) for operation in operations]
        return [future.result(timeout=30) for future in futures]


def snapshot(app):
    from extensions import db
    from models import Order, Purchase, User, WalletTransaction
    with app.app_context():
        return {"users": [(x.id, x.balance, x.frozen_balance) for x in User.query.order_by(User.id)],
                "orders": [(x.id, x.status, x.amount, x.commission) for x in Order.query.order_by(Order.id)],
                "purchases": [(x.user_id, x.toolkit_id, x.order_id) for x in Purchase.query.order_by(Purchase.id)],
                "ledger": [(x.user_id, x.type, x.amount, x.balance_after, x.order_id, x.event_key)
                           for x in WalletTransaction.query.order_by(WalletTransaction.id)]}


def balance(app, uid):
    from extensions import db
    from models import User
    with app.app_context():
        return db.session.get(User, uid).balance


def test_concurrent_create_reuses_one_pending(mysql_app, mysql_data):
    auth = headers(mysql_app, mysql_data)
    operations = [("POST", "/api/orders", auth, {"toolkitId": mysql_data["toolkits"]["paid"]})] * 4
    results = race(mysql_app, operations)
    assert all(code in (200, 201) for code, _ in results), results
    assert len({order["id"] for _, order in results}) == 1
    state = snapshot(mysql_app)
    assert len(state["orders"]) == 1 and state["orders"][0][1] == "pending"
    assert state["purchases"] == state["ledger"] == []


def test_concurrent_pay_once_with_exact_ledger(mysql_app, mysql_data):
    auth = headers(mysql_app, mysql_data)
    order = create_order(mysql_app, mysql_data, auth)
    results = race(mysql_app, [("POST", f"/api/orders/{order['id']}/pay", auth)] * 3)
    assert all(code == 200 for code, _ in results), results
    state = snapshot(mysql_app)
    assert state["orders"][0][1] == "paid"
    assert len(state["purchases"]) == 1
    assert len(state["ledger"]) == 2
    assert balance(mysql_app, mysql_data["users"]["buyer"]["id"]) == Decimal("200.00")
    assert balance(mysql_app, mysql_data["users"]["author"]["id"]) == Decimal("20.00")
    assert sorted((x[1], x[2], x[3]) for x in state["ledger"]) == [
        ("commission", Decimal("20.00"), Decimal("20.00")),
        ("purchase", Decimal("-100.00"), Decimal("200.00"))]
    assert len({x[5] for x in state["ledger"]}) == 2
    assert all(x[5] for x in state["ledger"])


def test_two_legacy_pending_cannot_charge_twice(mysql_app, mysql_data):
    from extensions import db
    from models import Order
    auth = headers(mysql_app, mysql_data)
    first = create_order(mysql_app, mysql_data, auth)
    with mysql_app.app_context():
        duplicate = Order(order_no=f"QA{uuid.uuid4().hex}", buyer_id=mysql_data["users"]["buyer"]["id"],
                          seller_id=mysql_data["users"]["author"]["id"], toolkit_id=mysql_data["toolkits"]["paid"],
                          amount=Decimal("100"), commission=Decimal("20"), status="pending")
        db.session.add(duplicate)
        db.session.commit()
        second_id = duplicate.id
    results = race(mysql_app, [("POST", f"/api/orders/{oid}/pay", auth) for oid in (first["id"], second_id)])
    assert sorted(code for code, _ in results) == [200, 409], results
    state = snapshot(mysql_app)
    assert sorted(x[1] for x in state["orders"]) == ["cancelled", "paid"]
    assert len(state["purchases"]) == 1 and len(state["ledger"]) == 2
    assert balance(mysql_app, mysql_data["users"]["buyer"]["id"]) == Decimal("200.00")


def test_pay_cancel_has_only_one_terminal_winner(mysql_app, mysql_data):
    auth = headers(mysql_app, mysql_data)
    order = create_order(mysql_app, mysql_data, auth)
    results = race(mysql_app, [("POST", f"/api/orders/{order['id']}/{action}", auth) for action in ("pay", "cancel")])
    assert all(code in (200, 409) for code, _ in results), results
    state = snapshot(mysql_app)
    status = state["orders"][0][1]
    assert status in {"paid", "cancelled"}
    assert len(state["ledger"]) == (2 if status == "paid" else 0)
    assert len(state["purchases"]) == (1 if status == "paid" else 0)
    assert balance(mysql_app, mysql_data["users"]["buyer"]["id"]) == Decimal(200 if status == "paid" else 300)


def test_pay_offline_preserves_valid_winner_and_access(mysql_app, mysql_data):
    buyer, author = headers(mysql_app, mysql_data), headers(mysql_app, mysql_data, "author")
    order = create_order(mysql_app, mysql_data, buyer)
    toolkit = mysql_data["toolkits"]["paid"]
    results = race(mysql_app, [("POST", f"/api/orders/{order['id']}/pay", buyer),
                               ("PUT", f"/api/toolkits/{toolkit}/status", author, {"status": "offline"})])
    assert results[1][0] == 200 and results[0][0] in (200, 409), results
    state = snapshot(mysql_app)
    paid = state["orders"][0][1] == "paid"
    assert len(state["ledger"]) == (2 if paid else 0)
    code, detail = request(mysql_app, "GET", f"/api/toolkits/{toolkit}", buyer)
    assert code == (200 if paid else 404), detail
    if paid:
        assert detail["canLearn"] and all(not x["locked"] for x in detail["lessons"])


def test_offline_pending_payment_fails_without_financial_effects(mysql_app, mysql_data):
    buyer, author = headers(mysql_app, mysql_data), headers(mysql_app, mysql_data, "author")
    order = create_order(mysql_app, mysql_data, buyer)
    code, _ = request(mysql_app, "PUT", f"/api/toolkits/{mysql_data['toolkits']['paid']}/status", author, {"status": "offline"})
    assert code == 200
    before = snapshot(mysql_app)
    code, _ = request(mysql_app, "POST", f"/api/orders/{order['id']}/pay", buyer)
    assert code == 409
    assert snapshot(mysql_app) == before


def test_two_orders_cannot_overspend_one_balance(mysql_app, mysql_data):
    from extensions import db
    from models import User
    auth = headers(mysql_app, mysql_data)
    with mysql_app.app_context():
        db.session.get(User, mysql_data["users"]["buyer"]["id"]).balance = Decimal("150")
        db.session.commit()
    orders = [create_order(mysql_app, mysql_data, auth, mysql_data["toolkits"][key]) for key in ("paid", "paidNoPreview")]
    results = race(mysql_app, [("POST", f"/api/orders/{order['id']}/pay", auth) for order in orders])
    assert sum(code == 200 for code, _ in results) == 1, results
    assert all(code in (200, 400, 409) for code, _ in results), results
    state = snapshot(mysql_app)
    assert len(state["ledger"]) == 2 and len(state["purchases"]) == 1
    assert balance(mysql_app, mysql_data["users"]["buyer"]["id"]) == Decimal("50")


def test_cross_trade_user_lock_order_has_no_deadlock(mysql_app, mysql_data):
    from extensions import db
    from models import Toolkit, User
    buyer, author = headers(mysql_app, mysql_data), headers(mysql_app, mysql_data, "author")
    with mysql_app.app_context():
        db.session.get(User, mysql_data["users"]["author"]["id"]).balance = Decimal("200")
        other = Toolkit(title="QA reverse sale", slug=f"reverse-{uuid.uuid4().hex}", summary="QA", description="QA",
                        category="生信分析", level="入门", author_id=mysql_data["users"]["buyer"]["id"],
                        price=Decimal("100"), status="published")
        db.session.add(other)
        db.session.commit()
        toolkit_id = other.id
    first = create_order(mysql_app, mysql_data, buyer)
    second = create_order(mysql_app, mysql_data, author, toolkit_id)
    results = race(mysql_app, [("POST", f"/api/orders/{first['id']}/pay", buyer),
                               ("POST", f"/api/orders/{second['id']}/pay", author)])
    assert [code for code, _ in results] == [200, 200], results
    assert balance(mysql_app, mysql_data["users"]["buyer"]["id"]) == Decimal("220")
    assert balance(mysql_app, mysql_data["users"]["author"]["id"]) == Decimal("120")
    assert len(snapshot(mysql_app)["ledger"]) == 4


@pytest.mark.parametrize("operation,amount", [("recharge", "40"), ("withdraw", "40")])
def test_wallet_same_request_key_is_exactly_once(mysql_app, mysql_data, operation, amount):
    from extensions import db
    from models import WalletReceipt
    auth = headers(mysql_app, mysql_data)
    key = f"qa-wallet-{uuid.uuid4().hex}"
    operations = [("POST", f"/api/wallet/{operation}", auth, {"amount": amount}, key)] * 3
    results = race(mysql_app, operations)
    assert all(code == 200 for code, _ in results), results
    state = snapshot(mysql_app)
    assert len(state["ledger"]) == 1
    expected = Decimal("340") if operation == "recharge" else Decimal("260")
    assert balance(mysql_app, mysql_data["users"]["buyer"]["id"]) == expected
    with mysql_app.app_context():
        assert db.session.query(WalletReceipt).count() == 1
    before = snapshot(mysql_app)
    code, _ = request(mysql_app, "POST", f"/api/wallet/{operation}", auth, {"amount": "50"}, key)
    assert code == 409 and snapshot(mysql_app) == before


def test_concurrent_distinct_recharges_do_not_lose_updates(mysql_app, mysql_data):
    auth = headers(mysql_app, mysql_data)
    results = race(mysql_app, [("POST", "/api/wallet/recharge", auth, {"amount": amount}, f"qa-{uuid.uuid4().hex}")
                               for amount in ("40", "50")])
    assert all(code == 200 for code, _ in results), results
    assert balance(mysql_app, mysql_data["users"]["buyer"]["id"]) == Decimal("390")
    ledger = snapshot(mysql_app)["ledger"]
    assert len(ledger) == 2 and ledger[-1][3] == Decimal("390")


def test_concurrent_withdrawals_respect_frozen_balance(mysql_app, mysql_data):
    from extensions import db
    from models import User
    auth = headers(mysql_app, mysql_data)
    with mysql_app.app_context():
        user = db.session.get(User, mysql_data["users"]["buyer"]["id"])
        # Existing business semantics: balance is AVAILABLE money; frozen is separate.
        user.balance, user.frozen_balance = Decimal("50"), Decimal("250")
        db.session.commit()
    results = race(mysql_app, [("POST", "/api/wallet/withdraw", auth, {"amount": "40"}, f"qa-{uuid.uuid4().hex}")] +
                              [("POST", "/api/wallet/withdraw", auth, {"amount": "40"}, f"qa-{uuid.uuid4().hex}")])
    assert sum(code == 200 for code, _ in results) == 1, results
    assert all(code in (200, 400, 409) for code, _ in results), results
    assert balance(mysql_app, mysql_data["users"]["buyer"]["id"]) == Decimal("10")
    with mysql_app.app_context():
        assert db.session.get(User, mysql_data["users"]["buyer"]["id"]).frozen_balance == Decimal("250")
    assert len(snapshot(mysql_app)["ledger"]) == 1


def test_payment_cannot_spend_separate_frozen_money(mysql_app, mysql_data):
    from extensions import db
    from models import User
    auth = headers(mysql_app, mysql_data)
    order = create_order(mysql_app, mysql_data, auth)
    with mysql_app.app_context():
        user = db.session.get(User, mysql_data["users"]["buyer"]["id"])
        user.balance, user.frozen_balance = Decimal("50"), Decimal("250")
        db.session.commit()
    before = snapshot(mysql_app)
    code, body = request(mysql_app, "POST", f"/api/orders/{order['id']}/pay", auth)
    assert code in (400, 409), body
    assert snapshot(mysql_app) == before


def test_password_change_wins_against_authenticated_waiting_payment(mysql_app, mysql_data, mysql_instance):
    from extensions import db
    auth = headers(mysql_app, mysql_data)
    order = create_order(mysql_app, mysql_data, auth)
    waiting, outcome = threading.Event(), []
    with mysql_app.app_context():
        engine = db.engine

    def observed(_conn, _cursor, statement, *_args):
        if threading.current_thread().name == "qa-pay" and "FOR UPDATE" in statement.upper() and re.search(r"FROM\s+`?toolkit`?", statement, re.I):
            waiting.set()

    event.listen(engine, "before_cursor_execute", observed)
    connection = mysql_instance.connect(mysql_instance.database)
    connection.begin()
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM toolkit WHERE id=%s FOR UPDATE", (mysql_data["toolkits"]["paid"],))
    worker = threading.Thread(name="qa-pay", target=lambda: outcome.append(request(mysql_app, "POST", f"/api/orders/{order['id']}/pay", auth)))
    worker.start()
    try:
        assert waiting.wait(5), "Payment never reached its toolkit lock"
        code, response = request(mysql_app, "PUT", "/api/auth/password", auth,
                                 {"oldPassword": mysql_data["users"]["buyer"]["password"], "newPassword": "QaChanged@123"})
        assert code == 200, response
    finally:
        connection.commit()
        connection.close()
        worker.join(timeout=20)
        event.remove(engine, "before_cursor_execute", observed)
    assert not worker.is_alive()
    assert outcome and outcome[0][0] == 401, outcome
    assert snapshot(mysql_app)["ledger"] == []
    assert balance(mysql_app, mysql_data["users"]["buyer"]["id"]) == Decimal("300")


def test_locked_wallet_reads_committed_balance_not_auth_snapshot(mysql_app, mysql_data, mysql_instance):
    from extensions import db
    auth = headers(mysql_app, mysql_data)
    uid = mysql_data["users"]["buyer"]["id"]
    waiting, outcome = threading.Event(), []
    with mysql_app.app_context():
        engine = db.engine

    def observed(_conn, _cursor, statement, *_args):
        if threading.current_thread().name == "qa-recharge" and "FOR UPDATE" in statement.upper() and re.search(r"FROM\s+`?user`?", statement, re.I):
            waiting.set()

    event.listen(engine, "before_cursor_execute", observed)
    connection = mysql_instance.connect(mysql_instance.database)
    connection.begin()
    with connection.cursor() as cursor:
        cursor.execute("UPDATE `user` SET balance=500 WHERE id=%s", (uid,))
    worker = threading.Thread(name="qa-recharge", target=lambda: outcome.append(request(mysql_app, "POST", "/api/wallet/recharge", auth,
                                             {"amount": "40"}, f"qa-{uuid.uuid4().hex}")))
    worker.start()
    try:
        assert waiting.wait(5), "Recharge never reached its user lock"
    finally:
        connection.commit()
        connection.close()
        worker.join(timeout=20)
        event.remove(engine, "before_cursor_execute", observed)
    assert not worker.is_alive()
    assert outcome and outcome[0][0] == 200, outcome
    assert balance(mysql_app, uid) == Decimal("540")
    assert snapshot(mysql_app)["ledger"][0][3] == Decimal("540")


def test_refund_rejection_has_zero_financial_side_effects(mysql_app, mysql_data):
    buyer, admin = headers(mysql_app, mysql_data), headers(mysql_app, mysql_data, "admin")
    order = create_order(mysql_app, mysql_data, buyer)
    assert request(mysql_app, "POST", f"/api/orders/{order['id']}/pay", buyer)[0] == 200
    before = snapshot(mysql_app)
    code, body = request(mysql_app, "POST", f"/api/admin/orders/{order['id']}/refund", admin)
    assert code == 403 and body.get("code") == "REFUNDS_DISABLED", body
    assert snapshot(mysql_app) == before
    code, _ = request(mysql_app, "POST", f"/api/admin/orders/{order['id']}/refund", buyer)
    assert code == 403 and snapshot(mysql_app) == before


def test_progress_upsert_real_concurrency_and_permission(mysql_app, mysql_data):
    from extensions import db
    from models import LessonProgress
    auth = headers(mysql_app, mysql_data)
    lesson = mysql_data["lessons"]["freeFirst"]
    results = race(mysql_app, [("POST", f"/api/lessons/{lesson}/complete", auth)] * 3)
    assert all(code == 200 for code, _ in results), results
    with mysql_app.app_context():
        assert db.session.query(LessonProgress).filter_by(lesson_id=lesson).count() == 1
    code, _ = request(mysql_app, "POST", f"/api/lessons/{mysql_data['lessons']['paidLocked']}/complete", auth)
    assert code == 403
    with mysql_app.app_context():
        assert db.session.query(LessonProgress).count() == 1


def _login_worker(config, run_id, username, attempts, connection):
    """Fresh interpreter + engine, not a copied in-process limiter or mock."""
    import os
    from app import create_app
    from extensions import db
    from sqlalchemy import text
    application = create_app(config)
    try:
        with application.app_context():
            marker = db.session.execute(text("SELECT run_id FROM __zhihe_test_marker")).scalar_one()
            assert marker == run_id
            db.session.remove()
        connection.send(("ready", os.getpid()))
        assert connection.recv() == "go"
        with application.test_client() as client:
            statuses = [client.post("/api/auth/login", json={"username": username, "password": "WrongQaPassword"}).status_code
                        for _ in range(attempts)]
        connection.send(("result", statuses))
    except Exception as error:
        connection.send(("error", type(error).__name__))
        raise
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()
        connection.close()


def _spawn_login_workers(app, mysql, work):
    import multiprocessing
    from test_support import isolated_config
    mysql.assert_owned()
    config = isolated_config(mysql.uri, app.config["UPLOAD_FOLDER"])
    config.update(JWT_SECRET_KEY=app.config["JWT_SECRET_KEY"], RATE_LIMIT_SECRET=app.config["RATE_LIMIT_SECRET"])
    context = multiprocessing.get_context("spawn")
    workers = []
    try:
        for username, attempts in work:
            parent, child = context.Pipe()
            process = context.Process(target=_login_worker, args=(config, mysql.run_id, username, attempts, child))
            process.start()
            child.close()
            workers.append((process, parent))
        pids = []
        for process, connection in workers:
            assert connection.poll(20), "Owned login worker did not become ready"
            kind, payload = connection.recv()
            assert kind == "ready", (kind, payload)
            pids.append(payload)
        assert len(set(pids)) == len(workers)
        for _process, connection in workers:
            connection.send("go")
        results = []
        for process, connection in workers:
            assert connection.poll(30), "Owned login worker did not finish"
            kind, payload = connection.recv()
            assert kind == "result", (kind, payload)
            results.append(payload)
            process.join(timeout=10)
            assert process.exitcode == 0
        mysql.assert_owned()
        return results
    finally:
        for process, connection in workers:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
            connection.close()


def test_login_limit_survives_worker_restart_and_collation_alias(mysql_app, mysql_data, mysql_instance):
    from extensions import db
    from models import RateLimitBucket, User
    username = mysql_data["users"]["buyer"]["username"]
    alias = username.upper().replace("A", "Á")
    with mysql_app.app_context():
        assert User.query.filter_by(username=alias).one().id == mysql_data["users"]["buyer"]["id"]
    first = _spawn_login_workers(mysql_app, mysql_instance, [(username, 4)])
    second = _spawn_login_workers(mysql_app, mysql_instance, [(alias, 5)])
    assert first == [[401] * 4]
    assert second == [[401] * 4 + [429]]
    with mysql_app.app_context():
        buckets = RateLimitBucket.query.all()
        assert len(buckets) == 4 and all(row.hits == 8 for row in buckets)


def test_shared_login_budget_is_atomic_across_processes(mysql_app, mysql_data, mysql_instance):
    from extensions import db
    from models import RateLimitBucket
    username = mysql_data["users"]["buyer"]["username"]
    results = _spawn_login_workers(mysql_app, mysql_instance, [(username, 6), (username.upper(), 6)])
    codes = [code for worker in results for code in worker]
    assert sorted(codes) == [401] * 8 + [429] * 4
    with mysql_app.app_context():
        buckets = RateLimitBucket.query.all()
        assert len(buckets) == 4 and all(row.hits == 8 for row in buckets)


def test_shared_rate_limit_ttl_cleanup_in_new_worker(mysql_app, mysql_data, mysql_instance):
    from datetime import timedelta
    from extensions import db
    from models import RateLimitBucket, utcnow
    username = mysql_data["users"]["buyer"]["username"]
    assert _spawn_login_workers(mysql_app, mysql_instance, [(username, 8)]) == [[401] * 8]
    with mysql_app.app_context():
        # Expire stored TTLs in the owned database without sleeping ten minutes.
        RateLimitBucket.query.update({RateLimitBucket.expires_at: utcnow() - timedelta(seconds=1)})
        db.session.commit()
    assert _spawn_login_workers(mysql_app, mysql_instance, [(username, 1)]) == [[401]]
    with mysql_app.app_context():
        buckets = RateLimitBucket.query.all()
        assert len(buckets) == 4 and all(row.hits == 1 and row.expires_at > utcnow() for row in buckets)
