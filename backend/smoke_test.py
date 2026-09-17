"""Run a small end-to-end API check against the configured local database."""
from app import Purchase, Toolkit, User, app


def auth(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['token']}"}


with app.test_client() as client:
    assert client.get("/api/health").status_code == 200
    user_headers = auth(client, "researcher", "User@123")
    admin_headers = auth(client, "admin", "Admin@123")

    listing = client.get("/api/toolkits", headers=user_headers)
    assert listing.status_code == 200 and listing.get_json()["total"] >= 6

    with app.app_context():
        paid_toolkit = Toolkit.query.filter(Toolkit.price > 0, Toolkit.author_id != 2).first()
        already_owned = Purchase.query.filter_by(user_id=2, toolkit_id=paid_toolkit.id).first()

    if not already_owned:
        created = client.post("/api/orders", json={"toolkitId": paid_toolkit.id}, headers=user_headers)
        assert created.status_code in (200, 201), created.get_json()
        paid = client.post(f"/api/orders/{created.get_json()['id']}/pay", headers=user_headers)
        assert paid.status_code == 200, paid.get_json()

    unlocked = client.get(f"/api/toolkits/{paid_toolkit.id}", headers=user_headers)
    assert unlocked.status_code == 200 and unlocked.get_json()["purchased"] is True
    assert all(not lesson["locked"] for lesson in unlocked.get_json()["lessons"])

    forbidden = client.get("/api/admin/stats", headers=user_headers)
    allowed = client.get("/api/admin/stats", headers=admin_headers)
    assert forbidden.status_code == 403 and allowed.status_code == 200

    print("Smoke test passed: auth, RBAC, listing, order payment, commission and unlock flow.")
