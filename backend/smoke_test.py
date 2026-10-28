"""Explicit API smoke check using an owned MySQL and freshly generated fixtures."""
from pathlib import Path
import tempfile


def main():
    from app import create_app
    from extensions import db
    from models import Order, Purchase, User, WalletTransaction
    from test_fixtures import RESOURCE, build_dataset, login_headers
    from test_support import IsolatedMySQL, isolated_config

    with IsolatedMySQL() as mysql, tempfile.TemporaryDirectory(prefix="zhihe-smoke-") as directory:
        application = create_app(isolated_config(mysql.uri, Path(directory) / "uploads"))
        with application.app_context():
            db.create_all()
            fixture = build_dataset(application, mysql.run_id)
        try:
            with application.test_client() as client:
                assert client.get("/api/health").status_code == 200
                buyer = login_headers(client, fixture["users"]["buyer"])
                author = login_headers(client, fixture["users"]["author"])
                admin = login_headers(client, fixture["users"]["admin"])
                free = client.get(f"/api/toolkits/{fixture['toolkits']['free']}")
                assert free.status_code == 200 and free.get_json()["canLearn"]
                assert all(x["content"] for x in free.get_json()["lessons"])
                created = client.post("/api/orders", json={"toolkitId": fixture["toolkits"]["paid"]}, headers=buyer)
                assert created.status_code == 201, created.get_json()
                order_id = created.get_json()["id"]
                paid = client.post(f"/api/orders/{order_id}/pay", headers=buyer)
                assert paid.status_code == 200, paid.get_json()
                assert client.post(f"/api/orders/{order_id}/pay", headers=buyer).status_code == 200
                assert client.get("/api/admin/stats", headers=buyer).status_code == 403
                assert client.get("/api/admin/stats", headers=admin).status_code == 200
                denied = client.post(f"/api/admin/orders/{order_id}/refund", headers=admin)
                assert denied.status_code == 403 and denied.get_json()["code"] == "REFUNDS_DISABLED"
                assert client.post(f"/api/lessons/{fixture['lessons']['paidLocked']}/complete", headers=buyer).status_code == 200
                offline = client.put(f"/api/toolkits/{fixture['toolkits']['paid']}/status", json={"status": "offline"}, headers=author)
                assert offline.status_code == 200
                detail = client.get(f"/api/toolkits/{fixture['toolkits']['paid']}", headers=buyer)
                assert detail.status_code == 200 and all(not x["locked"] for x in detail.get_json()["lessons"])
                download = client.get(f"/api/toolkits/{fixture['toolkits']['paid']}/download", headers=buyer)
                assert download.status_code == 200, download.get_json()
                assert download.data == RESOURCE, "Fixture attachment body was not preserved"
                download.close()
                with application.app_context():
                    assert db.session.get(User, fixture["users"]["buyer"]["id"]).balance == 200
                    assert db.session.get(User, fixture["users"]["author"]["id"]).balance == 20
                    assert db.session.query(Order).count() == 1
                    assert db.session.query(Purchase).count() == 1
                    assert db.session.query(WalletTransaction).count() == 2
            mysql.assert_owned()
            print("Isolated smoke passed: auth, RBAC, free learning, payment/commission, no refunds, offline rights and download.")
        finally:
            with application.app_context():
                db.session.remove()
                db.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
