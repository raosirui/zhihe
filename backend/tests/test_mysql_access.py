"""Actual MySQL authorization matrix, with paid transactions and active assets."""
import pytest

from test_fixtures import RESOURCE, SVG, login_headers
from test_mysql_regression import create_order, request, snapshot

pytestmark = pytest.mark.mysql


@pytest.mark.parametrize("kind", ["free", "preview", "paid", "offline"])
@pytest.mark.parametrize("role", ["anonymous", "unpurchased", "purchased", "author", "admin"])
def test_mysql_body_asset_download_and_progress_matrix(mysql_app, mysql_data, kind, role):
    with mysql_app.test_client() as client:
        buyer = login_headers(client, mysql_data["users"]["buyer"])
        author = login_headers(client, mysql_data["users"]["author"])
    paid_key = "paidNoPreview" if kind == "paid" else "paid"
    paid_id = mysql_data["toolkits"][paid_key]
    order = create_order(mysql_app, mysql_data, buyer, paid_id)
    assert request(mysql_app, "POST", f"/api/orders/{order['id']}/pay", buyer)[0] == 200
    if kind == "offline":
        assert request(mysql_app, "PUT", f"/api/toolkits/{paid_id}/status", author, {"status": "offline"})[0] == 200
    toolkit = mysql_data["toolkits"]["free"] if kind == "free" else paid_id
    if role == "anonymous":
        auth = {}
    elif role == "purchased":
        auth = buyer
    elif role == "author":
        auth = author
    else:
        with mysql_app.test_client() as client:
            auth = login_headers(client, mysql_data["users"]["legacy" if role == "unpurchased" else "admin"])
    manager = role in {"author", "admin"}
    entitled = manager or role == "purchased"
    visible = kind != "offline" or entitled
    full = kind == "free" or entitled
    partial = kind == "preview"
    before = snapshot(mysql_app)
    with mysql_app.test_client() as client:
        response = client.get(f"/api/toolkits/{toolkit}", headers=auth)
        assert response.status_code == (200 if visible else 404)
        if visible:
            detail = response.get_json()
            assert detail["canLearn"] is full
            assert detail["canDownload"] is (full and role != "anonymous")
            assert detail["purchased"] is (role == "purchased" and kind != "free")
            for lesson in detail["lessons"]:
                allowed = full or (partial and lesson["isPreview"])
                assert lesson["canLearn"] is allowed
                assert lesson["locked"] is not allowed
                assert bool(lesson["content"]) is allowed
                assert bool(lesson["image"]) is (allowed and lesson["title"] != "Free second")
                if lesson["image"]:
                    assert "token=" not in lesson["image"] and "jwt=" not in lesson["image"]
        asset_key = "free" if kind == "free" else "preview" if kind == "preview" else "locked"
        stored = mysql_data["assets"][asset_key]
        image = client.get(f"/api/lesson-asset/{stored}", headers=auth)
        image_allowed = full or partial
        if image_allowed:
            assert image.status_code == 200 and image.data == SVG
        else:
            assert image.status_code in {401, 403, 404} and image.data != SVG
        assert image.headers["Cache-Control"] == "no-store"
        image.close()
        # No private registered asset may bypass authorization via old public URL.
        public = client.get(f"/api/uploads/{stored}", headers=auth)
        assert public.status_code == 404
        download = client.get(f"/api/toolkits/{toolkit}/download", headers=auth)
        if full and role != "anonymous":
            assert download.status_code == 200 and download.data == RESOURCE
            assert "attachment" in download.headers["Content-Disposition"]
        else:
            assert download.status_code in {401, 403, 404} and download.data != RESOURCE
        download.close()
        lesson_key = "freeFirst" if kind == "free" else "paidPreview" if kind == "preview" else "paidNoPreview" if kind == "paid" else "paidLocked"
        completed = client.post(f"/api/lessons/{mysql_data['lessons'][lesson_key]}/complete", headers=auth)
        if role == "anonymous":
            assert completed.status_code == 401
        elif full or partial:
            assert completed.status_code == 200
        else:
            assert completed.status_code in {403, 404}
    assert snapshot(mysql_app) == before, "Authorization checks must not change financial state"
