"""Order seller foreign keys must not reverse the sorted User lock order."""
from decimal import Decimal
import re
import threading
import time

import pytest
from sqlalchemy import event

from test_fixtures import login_headers
from test_mysql_regression import balance, create_order, request, snapshot

pytestmark = pytest.mark.mysql


def test_cross_toolkit_create_and_pay_lock_both_users_before_order_foreign_keys(mysql_app, mysql_data, mysql_instance):
    from extensions import db
    buyer_id = mysql_data["users"]["legacy"]["id"]
    seller_id = mysql_data["users"]["author"]["id"]
    assert seller_id < buyer_id
    with mysql_app.test_client() as client:
        auth = login_headers(client, mysql_data["users"]["legacy"])
    existing = create_order(mysql_app, mysql_data, auth, mysql_data["toolkits"]["paidNoPreview"])
    creation_locked, release, pay_attempted = threading.Event(), threading.Event(), threading.Event()
    locked_ids, outcomes = [], {}
    with mysql_app.app_context():
        engine = db.engine

    def user_lock(statement):
        return "FOR UPDATE" in statement.upper() and re.search(r"FROM\s+`?user`?\b", statement, re.I)

    def parameter_ids(parameters):
        values = parameters.values() if isinstance(parameters, dict) else parameters
        return [value for value in values if type(value) is int]

    def after_create(_connection, _cursor, statement, parameters, *_args):
        if threading.current_thread().name == "qa-fk-create" and user_lock(statement):
            ids = parameter_ids(parameters)
            locked_ids.extend(ids)
            if buyer_id in ids:
                creation_locked.set()
                assert release.wait(15), "Owned creation worker was not released"

    def before_pay(_connection, _cursor, statement, parameters, *_args):
        if threading.current_thread().name == "qa-fk-pay" and user_lock(statement) and seller_id in parameter_ids(parameters):
            pay_attempted.set()

    event.listen(engine, "after_cursor_execute", after_create)
    event.listen(engine, "before_cursor_execute", before_pay)
    creator = threading.Thread(name="qa-fk-create", target=lambda: outcomes.update(create=request(
        mysql_app, "POST", "/api/orders", auth, {"toolkitId": mysql_data["toolkits"]["paid"]})))
    payer = threading.Thread(name="qa-fk-pay", target=lambda: outcomes.update(pay=request(
        mysql_app, "POST", f"/api/orders/{existing['id']}/pay", auth)))
    creator.start()
    try:
        assert creation_locked.wait(5)
        # The old code only locked buyer, then INSERT's seller FK locked the
        # lower seller ID after it. Here both explicit locks must be sorted.
        assert locked_ids == [seller_id, buyer_id], locked_ids
        payer.start()
        assert pay_attempted.wait(5)
        deadline = time.monotonic() + 5
        observed_wait = False
        with mysql_instance.connect(mysql_instance.database) as connection:
            with connection.cursor() as cursor:
                while time.monotonic() < deadline:
                    cursor.execute("SELECT COUNT(*) FROM performance_schema.data_lock_waits w "
                                   "JOIN performance_schema.data_locks r ON r.ENGINE_LOCK_ID=w.REQUESTING_ENGINE_LOCK_ID "
                                   "WHERE r.OBJECT_SCHEMA=%s AND r.OBJECT_NAME='user'", (mysql_instance.database,))
                    if cursor.fetchone()[0]:
                        observed_wait = True
                        break
                    time.sleep(0.02)
        assert observed_wait, "Payment did not really wait for the owned create transaction's User lock"
    finally:
        release.set()
        creator.join(timeout=20)
        if payer.ident is not None:
            payer.join(timeout=20)
        event.remove(engine, "after_cursor_execute", after_create)
        event.remove(engine, "before_cursor_execute", before_pay)
    assert not creator.is_alive() and not payer.is_alive()
    if outcomes["create"][0] != 201 or outcomes["pay"][0] != 200:
        # This server contains only this fixture; never inspect system/real DB locks.
        mysql_instance.assert_owned()
        with mysql_instance.connect(mysql_instance.database) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SHOW ENGINE INNODB STATUS")
                status = cursor.fetchone()[2]
        begin = status.find("LATEST DETECTED DEADLOCK")
        end = status.find("TRANSACTIONS", begin + 1)
        details = status[begin:end][:12000] if begin >= 0 else "No owned deadlock report"
        pytest.fail(f"Cross-course create/pay failed: {outcomes}\n{details}")
    state = snapshot(mysql_app)
    assert sorted(order[1] for order in state["orders"]) == ["paid", "pending"]
    assert len(state["ledger"]) == 2 and len(state["purchases"]) == 1
    assert balance(mysql_app, buyer_id) == Decimal("200.00")
    assert balance(mysql_app, seller_id) == Decimal("20.00")
    mysql_instance.assert_owned()
