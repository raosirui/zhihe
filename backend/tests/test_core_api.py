"""Fast behavioral regressions. SQLite is NOT concurrent-locking evidence."""
import io
import json
import os
import subprocess
import sys
import zipfile
from decimal import Decimal
from pathlib import Path
import pytest
from flask_jwt_extended import create_access_token
from PIL import Image
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, SessionTransaction
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import (create_app, db, User, Toolkit, Lesson, Order, Purchase, WalletTransaction,
                 WalletReceipt, LessonProgress, RevokedToken, FileAsset, FileReference, FileOperation,
                 RateLimitBucket, seed_data)


@pytest.fixture
def env(tmp_path):
    app = create_app({"APP_ENV": "test", "TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                      "UPLOAD_FOLDER": str(tmp_path / "uploads"), "JWT_SECRET_KEY": "isolated-test-secret-at-least-32-characters",
                      "WALLET_MODE": "simulation"})
    with app.app_context():
        db.create_all()
        users = [User(username=name, nickname=name, role=role, balance=balance,
                      password_hash=generate_password_hash(password)) for name, role, balance, password in [
                      ("admin", "admin", 0, "Admin123"), ("seller", "user", 0, "Seller123"),
                      ("buyer", "user", 200, "Buyer123"), ("other", "user", 0, "Other123"),
                      ("legacy", "user", 0, "six123")]]
        db.session.add_all(users)
        db.session.commit()
        ids = {x.username: x.id for x in users}
        headers = {x.username: {"Authorization": "Bearer " + create_access_token(identity=str(x.id),
                    additional_claims={"token_version": x.token_version})} for x in users}
    yield app, app.test_client(), ids, headers, tmp_path / "uploads"
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def make_toolkit(env, price=10, resource=None):
    _, client, _, headers, _ = env
    fields = {"title": "Toolkit " + str(price), "description": "description", "price": str(price),
              "lessons": [{"title": "preview", "content": "public body", "preview": True},
                          {"title": "full", "content": "paid body", "preview": False}]}
    if resource:
        fields["lessons"] = json.dumps(fields["lessons"])
        fields["resource"] = (io.BytesIO(resource), "tool.py")
        response = client.post("/api/toolkits", data=fields, headers=headers["seller"])
    else:
        response = client.post("/api/toolkits", json=fields, headers=headers["seller"])
    assert response.status_code == 201, response.json
    return response.json


def buy(env, toolkit):
    _, client, _, headers, _ = env
    response = client.post("/api/orders", json={"toolkitId": toolkit["id"]}, headers=headers["buyer"])
    assert response.status_code == 201, response.json
    oid = response.json["id"]
    assert client.post(f"/api/orders/{oid}/pay", headers=headers["buyer"]).status_code == 200
    return oid


def png_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(stream, "PNG")
    return stream.getvalue()


def package(items, assets=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("toolkits.json", json.dumps(items))
        for name, body in (assets or {}).items():
            archive.writestr(name, body)
    stream.seek(0)
    return stream


def batch_item(title, **extra):
    return {"title": title, "description": "description", "price": 10,
            "lessons": [{"title": "preview", "content": "public body", "preview": True},
                        {"title": "full", "content": "paid body", "preview": False}], **extra}


def test_import_and_factory_no_io(tmp_path):
    script = """
import os,socket
os.environ['DATABASE_URL']='mysql+pymysql://forbidden:forbidden@127.0.0.1/existing'
def forbidden(*args,**kwargs): raise AssertionError('unexpected IO')
socket.socket.connect=forbidden
import dotenv
dotenv.load_dotenv=forbidden
import app
os.makedirs=forbidden
application=app.create_app({'APP_ENV':'test','TESTING':True,'SQLALCHEMY_DATABASE_URI':'sqlite:///:memory:','UPLOAD_FOLDER':%r,'JWT_SECRET_KEY':'isolated-only-test-key-with-32-chars'})
assert application.config['SQLALCHEMY_DATABASE_URI']=='sqlite:///:memory:'
assert application.config['JWT_TOKEN_LOCATION']==['headers']
""" % str(tmp_path / "must-not-exist")
    subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONPATH": str(ROOT),
                   "PYTHONDONTWRITEBYTECODE": "1"}, check=True, capture_output=True)
    assert not (tmp_path / "must-not-exist").exists()


@pytest.mark.parametrize("changes", [{}, {"JWT_SECRET_KEY": "replace-with-at-least-32-random-characters"},
    {"DEBUG": True}, {"WALLET_MODE": "simulation"}, {"DEMO_SEED": True}])
def test_production_fail_fast(changes):
    config = {"APP_ENV": "production", "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", **changes}
    if changes and "JWT_SECRET_KEY" not in changes:
        config["JWT_SECRET_KEY"] = "strong-production-random-like-key-32-characters"
    with pytest.raises(ValueError):
        create_app(config)


def test_development_key_and_production_database_explicit():
    with pytest.raises(ValueError):
        create_app({"APP_ENV": "development", "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with pytest.raises(ValueError):
        create_app({"APP_ENV": "production", "JWT_SECRET_KEY": "strong-explicit-production-key-32-characters"})


def test_disabled_money_blocks_new_funds_but_keeps_history_access(env):
    app, client, _, headers, _ = env
    paid = make_toolkit(env)
    oid = buy(env, paid)
    pending_kit = make_toolkit(env, price=20)
    pending = client.post("/api/orders", json={"toolkitId": pending_kit["id"]}, headers=headers["buyer"]).json["id"]
    app.config["WALLET_MODE"] = "disabled"
    assert client.post("/api/orders", json={"toolkitId": pending_kit["id"]}, headers=headers["buyer"]).status_code == 403
    assert client.post(f"/api/orders/{pending}/pay", headers=headers["buyer"]).status_code == 403
    assert client.post("/api/wallet/recharge", json={"amount": 10}, headers={**headers["buyer"], "Idempotency-Key": "off"}).status_code == 403
    assert client.post("/api/wallet/withdraw", json={"amount": 10}, headers={**headers["buyer"], "Idempotency-Key": "off"}).status_code == 403
    assert client.post(f"/api/orders/{oid}/pay", headers=headers["buyer"]).status_code == 200
    assert client.post(f"/api/orders/{pending}/cancel", headers=headers["buyer"]).status_code == 200
    assert client.get(f"/api/toolkits/{paid['id']}", headers=headers["buyer"]).json["canLearn"]
    wallet = client.get("/api/wallet", headers=headers["buyer"]).json
    assert not any(wallet[key] for key in ["demoEnabled", "canRecharge", "canWithdraw", "canPay"])


def test_no_implicit_seed(env):
    app, _, _, _, uploads = env
    assert not uploads.exists()
    with app.app_context(), pytest.raises(RuntimeError):
        seed_data()


@pytest.mark.parametrize("amount", [True, False, {}, [], None, "NaN", "Infinity", "-Infinity", "1e100", "0.001"])
def test_finite_amounts_and_atomic_noop(env, amount):
    app, client, ids, headers, _ = env
    response = client.post("/api/wallet/recharge", json={"amount": amount},
                           headers={**headers["buyer"], "Idempotency-Key": "invalid"})
    assert response.status_code == 400, response.json
    with app.app_context():
        assert db.session.get(User, ids["buyer"]).balance == Decimal("200")
        assert WalletTransaction.query.count() == 0
        assert WalletReceipt.query.count() == 0


def test_wallet_key_replay_and_conflict(env):
    app, client, ids, headers, _ = env
    assert client.post("/api/wallet/recharge", json={"amount": 10}, headers=headers["buyer"]).status_code == 400
    h = {**headers["buyer"], "Idempotency-Key": "recharge-1"}
    first = client.post("/api/wallet/recharge", json={"amount": "10.00"}, headers=h)
    again = client.post("/api/wallet/recharge", json={"amount": 10}, headers=h)
    assert first.status_code == again.status_code == 200
    assert first.json == again.json
    assert client.post("/api/wallet/recharge", json={"amount": 11}, headers=h).status_code == 409
    h = {**headers["buyer"], "Idempotency-Key": "withdraw-1"}
    assert client.post("/api/wallet/withdraw", json={"amount": 10}, headers=h).status_code == 200
    assert client.post("/api/wallet/withdraw", json={"amount": 10}, headers=h).status_code == 200
    with app.app_context():
        assert db.session.get(User, ids["buyer"]).balance == Decimal("200")
        assert WalletTransaction.query.count() == WalletReceipt.query.count() == 2
        assert all(x.event_key for x in WalletTransaction.query.all())


@pytest.mark.parametrize("header", ["", "Basic abc", "Bearer", "Bearer invalid", ",,,"])
def test_present_invalid_optional_auth_never_becomes_anonymous(env, header):
    _, client, _, _, _ = env
    response = client.get("/api/toolkits", headers={"Authorization": header})
    assert response.status_code == 401, response.json
    assert response.headers["Cache-Control"] == "no-store"


def test_frozen_is_separate_and_receipts_survive_hmac_key_rotation(env):
    app, client, ids, headers, _ = env
    with app.app_context():
        user = db.session.get(User, ids["buyer"])
        user.balance, user.frozen_balance = Decimal("50"), Decimal("250")
        db.session.commit()
    h = {**headers["buyer"], "Idempotency-Key": "rotate-proof"}
    assert client.post("/api/wallet/withdraw", json={"amount": 40}, headers=h).status_code == 200
    app.config["RATE_LIMIT_SECRET"] = "another-hmac-secret-32-characters"
    assert client.post("/api/wallet/withdraw", json={"amount": 40}, headers=h).status_code == 200
    assert client.post("/api/wallet/withdraw", json={"amount": 40}, headers={**headers["buyer"], "Idempotency-Key": "second"}).status_code == 400
    toolkit = make_toolkit(env, price=20)
    pending = client.post("/api/orders", json={"toolkitId": toolkit["id"]}, headers=headers["buyer"]).json["id"]
    assert client.post(f"/api/orders/{pending}/pay", headers=headers["buyer"]).status_code == 400
    with app.app_context():
        assert db.session.get(User, ids["buyer"]).balance == Decimal("10")
        assert db.session.get(User, ids["buyer"]).frozen_balance == Decimal("250")
        assert WalletTransaction.query.count() == WalletReceipt.query.count() == 1


def test_short_password_login_and_version_revoke(env):
    app, client, ids, headers, _ = env
    assert client.post("/api/auth/login", json={"username": "legacy", "password": "six123"}).status_code == 200
    assert client.post("/api/auth/register", json={"username": "new", "password": "six123"}).status_code == 400
    second = client.post("/api/auth/login", json={"username": "buyer", "password": "Buyer123"}).json["token"]
    assert client.put("/api/auth/password", json={"oldPassword": "Buyer123", "newPassword": "NewBuyer123"}, headers=headers["buyer"]).status_code == 200
    for header in [headers["buyer"], {"Authorization": "Bearer " + second}]:
        assert client.get("/api/auth/me", headers=header).status_code == 401
        assert client.get("/api/toolkits", headers=header).status_code == 401
        assert client.get("/api/toolkits", headers=header).headers["Cache-Control"] == "no-store"
    with app.app_context():
        assert db.session.get(User, ids["buyer"]).token_version == 1
        no_version = create_access_token(identity=str(ids["other"]))
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + no_version}).status_code == 401
    assert client.post("/api/auth/logout", headers=headers["other"]).status_code == 200
    assert client.get("/api/auth/me", headers=headers["other"]).status_code == 401


@pytest.mark.parametrize("path,method", [("/api/auth/login", "post"), ("/api/auth/register", "post"),
    ("/api/auth/password", "put"), ("/api/orders", "post"), ("/api/wallet/recharge", "post"),
    ("/api/toolkits/1/status", "put"), ("/api/admin/toolkits/1/featured", "post")])
@pytest.mark.parametrize("body", [[], "string", True, 3, None])
def test_json_object_required(env, path, method, body):
    _, client, _, headers, _ = env
    response = getattr(client, method)(path, data=json.dumps(body), content_type="application/json", headers=headers["admin"])
    assert response.status_code == 400, response.json


def test_order_retries_duplicate_history_and_refunds_noop(env):
    app, client, ids, headers, _ = env
    toolkit = make_toolkit(env)
    first = client.post("/api/orders", json={"toolkitId": toolkit["id"]}, headers=headers["buyer"])
    oid = first.json["id"]
    again = client.post("/api/orders", json={"toolkitId": toolkit["id"]}, headers=headers["buyer"])
    assert again.status_code == 200 and again.json["id"] == oid
    with app.app_context():
        duplicate = Order(order_no="historic-pending", buyer_id=ids["buyer"], seller_id=ids["seller"],
                          toolkit_id=toolkit["id"], amount=10, commission=2, status="pending")
        db.session.add(duplicate)
        db.session.commit()
        duplicate_id = duplicate.id
    for _ in range(2):
        assert client.post(f"/api/orders/{oid}/pay", headers=headers["buyer"]).status_code == 200
    assert client.post(f"/api/orders/{duplicate_id}/pay", headers=headers["buyer"]).status_code == 409
    assert client.post(f"/api/orders/{oid}/cancel", headers=headers["buyer"]).status_code == 409
    with app.app_context():
        before = (db.session.get(User, ids["buyer"]).balance, db.session.get(User, ids["seller"]).balance,
                  WalletTransaction.query.count(), Purchase.query.count(), db.session.get(Order, oid).status)
        assert before == (Decimal("190"), Decimal("2"), 2, 1, "paid")
        assert db.session.get(Order, duplicate_id).status == "cancelled"
    refund = client.post(f"/api/admin/orders/{oid}/refund", headers=headers["admin"])
    assert refund.status_code == 403 and refund.json["code"] == "REFUNDS_DISABLED"
    with app.app_context():
        after = (db.session.get(User, ids["buyer"]).balance, db.session.get(User, ids["seller"]).balance,
                 WalletTransaction.query.count(), Purchase.query.count(), db.session.get(Order, oid).status)
        assert before == after
        # Paid historical retry must NOT regenerate absent history.
        Purchase.query.delete()
        WalletTransaction.query.delete()
        db.session.commit()
    assert client.post(f"/api/orders/{oid}/pay", headers=headers["buyer"]).status_code == 200
    with app.app_context():
        assert Purchase.query.count() == WalletTransaction.query.count() == 0


def test_offline_paid_access_and_unpaid_recheck(env):
    app, client, _, headers, _ = env
    toolkit = make_toolkit(env, resource=b"print('attachment only')")
    oid = buy(env, toolkit)
    assert client.put(f"/api/toolkits/{toolkit['id']}/status", json={"status": "offline"}, headers=headers["seller"]).status_code == 200
    assert client.get(f"/api/toolkits/{toolkit['id']}").status_code == 404
    assert client.get(f"/api/toolkits/{toolkit['id']}", headers=headers["other"]).status_code == 404
    for who in ["buyer", "seller", "admin"]:
        response = client.get(f"/api/toolkits/{toolkit['id']}", headers=headers[who])
        assert response.status_code == 200
        assert all(not x["locked"] for x in response.json["lessons"])
        download = client.get(f"/api/toolkits/{toolkit['id']}/download", headers=headers[who])
        assert download.status_code == 200
        assert "attachment" in download.headers["Content-Disposition"]
        download.close()
    assert client.post(f"/api/orders/{oid}/pay", headers=headers["buyer"]).status_code == 200
    second = make_toolkit(env, price=20)
    pending = client.post("/api/orders", json={"toolkitId": second["id"]}, headers=headers["buyer"]).json["id"]
    client.put(f"/api/toolkits/{second['id']}/status", json={"status": "offline"}, headers=headers["seller"])
    response = client.post(f"/api/orders/{pending}/pay", headers=headers["buyer"])
    assert response.status_code == 409 and response.json["code"] == "TOOLKIT_NOT_FOR_SALE"
    assert client.post(f"/api/orders/{pending}/cancel", headers=headers["buyer"]).status_code == 200
    assert client.post(f"/api/orders/{pending}/cancel", headers=headers["buyer"]).status_code == 200


def test_free_favorite_complete_and_download_count(env):
    app, client, _, headers, uploads = env
    free = make_toolkit(env, price=0, resource=b"free bytes")
    assert all(x["content"] for x in client.get(f"/api/toolkits/{free['id']}").json["lessons"])
    assert client.get(f"/api/toolkits/{free['id']}/download").status_code == 401
    assert client.post(f"/api/toolkits/{free['id']}/favorite", headers=headers["buyer"]).json["favorited"]
    detail = client.get(f"/api/toolkits/{free['id']}", headers=headers["buyer"]).json
    assert detail["favorited"] and not detail["purchased"] and detail["canLearn"] and detail["canDownload"]
    lid = detail["lessons"][1]["id"]
    for _ in range(2):
        assert client.post(f"/api/lessons/{lid}/complete", headers=headers["buyer"]).status_code == 200
    with app.app_context():
        assert LessonProgress.query.count() == 1
        stored = db.session.get(Toolkit, free["id"]).resource_path
    download = client.get(f"/api/toolkits/{free['id']}/download", headers=headers["buyer"])
    assert download.status_code == 200 and download.data == b"free bytes"
    download.close()
    (uploads / "resource" / stored).unlink()
    assert client.get(f"/api/toolkits/{free['id']}/download", headers=headers["buyer"]).status_code == 404
    with app.app_context():
        assert db.session.get(Toolkit, free["id"]).downloads == 1
    paid = make_toolkit(env, price=30)
    assert client.post(f"/api/lessons/{paid['lessons'][1]['id']}/complete", headers=headers["buyer"]).status_code == 403
    assert client.post(f"/api/lessons/{paid['lessons'][0]['id']}/complete", headers=headers["buyer"]).status_code == 200


def test_file_batch_shared_cover_and_protected_images(env):
    app, client, _, headers, uploads = env
    first = batch_item("first", cover="shared.png")
    first["lessons"][0]["image"] = "shared.png"
    first["lessons"][1]["image"] = "private.png"
    response = client.post("/api/batch/upload", data={"package": (package([first, batch_item("second", cover="shared.png")],
                           {"shared.png": png_bytes(), "private.png": png_bytes()}), "package.zip")}, headers=headers["seller"])
    assert response.status_code == 200 and response.json["created"] == 2, response.json
    tids = [x["toolkitId"] for x in response.json["results"]]
    detail = client.get(f"/api/toolkits/{tids[0]}", headers=headers["seller"]).json
    assert "?" not in detail["lessons"][0]["image"]
    assert client.get(detail["lessons"][0]["image"]).status_code == 200
    assert client.get(detail["lessons"][1]["image"]).status_code == 403
    assert client.get(detail["lessons"][1]["image"] + "?t=" + headers["seller"]["Authorization"][7:]).status_code == 403
    assert client.get(detail["lessons"][1]["image"], headers=headers["seller"]).status_code == 200
    shared = uploads / "image" / detail["cover"].split("/")[-1]
    assert shared.exists()
    assert client.delete(f"/api/toolkits/{tids[0]}", headers=headers["seller"]).json["mode"] == "deleted"
    assert shared.exists() and client.get(detail["cover"]).status_code == 200
    # Public reuse creates a reference, not ownership/deletion capabilities.
    reuse = client.post("/api/toolkits", json={"title": "reuse", "description": "desc", "cover": detail["cover"]}, headers=headers["other"])
    assert reuse.status_code == 201, reuse.json
    assert client.delete(f"/api/toolkits/{tids[1]}", headers=headers["seller"]).json["mode"] == "deleted"
    assert shared.exists()
    assert client.delete(f"/api/toolkits/{reuse.json['id']}", headers=headers["other"]).status_code == 200
    assert not shared.exists()
    with app.app_context():
        assert FileReference.query.count() == 0
        assert FileOperation.query.count() > 0


@pytest.mark.parametrize("bad", [b"not a png", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<!DOCTYPE svg [<!ENTITY a "hi">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/a"/></svg>'])
def test_actual_image_and_svg_failures_cleanup(env, bad):
    app, client, _, headers, uploads = env
    name = "bad.svg" if bad.startswith(b"<") else "bad.png"
    response = client.post("/api/batch/upload", data={"package": (package([batch_item("bad", cover=name)], {name: bad}), "bad.zip")}, headers=headers["seller"])
    assert response.status_code == 200 and response.json["created"] == 0 and response.json["failed"] == 1, response.json
    with app.app_context():
        assert Toolkit.query.count() == 0
        assert FileReference.query.count() == 0
        assert all(x.state == "deleted" for x in FileAsset.query.all())
    assert not list((uploads / "image").glob("*"))


def test_batch_counts_skipped_and_commit_ack_recovery(env, monkeypatch):
    app, client, _, headers, _ = env
    archive = lambda: package([batch_item("one"), batch_item("bad", price="NaN")])
    first = client.post("/api/batch/upload", data={"package": (archive(), "pack.zip")}, headers=headers["seller"])
    assert (first.json["created"], first.json["failed"], first.json["skipped"]) == (1, 1, 0)
    again = client.post("/api/batch/upload", data={"package": (archive(), "pack.zip")}, headers=headers["seller"])
    assert (again.json["created"], again.json["failed"], again.json["skipped"]) == (0, 1, 1)
    original = SessionTransaction.commit
    injected = {"done": False}
    def lost_ack(transaction, *args, **kwargs):
        business = any(isinstance(x, Toolkit) for x in transaction.session.identity_map.values())
        result = original(transaction, *args, **kwargs)
        if business and transaction.parent is None and not injected["done"]:
            injected["done"] = True
            raise SQLAlchemyError("simulated lost commit acknowledgement")
        return result
    monkeypatch.setattr(SessionTransaction, "commit", lost_ack)
    response = client.post("/api/toolkits", json={"title": "committed", "description": "desc"}, headers=headers["seller"])
    assert injected["done"] and response.status_code == 201, response.json
    with app.app_context():
        assert Toolkit.query.filter_by(title="committed").count() == 1


def test_valid_passive_svg_compatibility(env):
    _, client, _, headers, _ = env
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40" viewBox="0 0 80 40"><defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient></defs><rect width="80" height="40" fill="url(#g)"/><text x="5" y="20">safe</text></svg>'
    response = client.post("/api/batch/upload", data={"package": (package([batch_item("passive", cover="safe.svg")], {"safe.svg": svg}), "svg.zip")}, headers=headers["seller"])
    assert response.status_code == 200 and response.json["created"] == 1, response.json
    tid = response.json["results"][0]["toolkitId"]
    cover = client.get(f"/api/toolkits/{tid}").json["cover"]
    image = client.get(cover)
    assert image.status_code == 200 and image.data == svg
    assert "sandbox" in image.headers["Content-Security-Policy"]


def test_known_database_rollback_keeps_old_file_and_cleans_only_new(env, monkeypatch):
    app, client, _, headers, uploads = env
    toolkit = make_toolkit(env, resource=b"old persistent bytes")
    with app.app_context():
        old = db.session.get(Toolkit, toolkit["id"]).resource_path
    original = SessionTransaction.commit
    injected = {"done": False}
    def fail_before_commit(transaction, *args, **kwargs):
        business = any(isinstance(x, Toolkit) for x in transaction.session.identity_map.values())
        if business and transaction.parent is None and not injected["done"]:
            injected["done"] = True
            raise SQLAlchemyError("isolated known rollback")
        return original(transaction, *args, **kwargs)
    monkeypatch.setattr(SessionTransaction, "commit", fail_before_commit)
    response = client.put(f"/api/toolkits/{toolkit['id']}", data={"title": "must roll back", "description": "desc",
                          "resource": (io.BytesIO(b"new uncommitted bytes"), "new.py")}, headers=headers["seller"])
    assert injected["done"] and response.status_code == 503, response.json
    assert (uploads / "resource" / old).read_bytes() == b"old persistent bytes"
    assert len(list((uploads / "resource").iterdir())) == 1
    with app.app_context():
        assert db.session.get(Toolkit, toolkit["id"]).resource_path == old
        assert FileReference.query.filter_by(usage="resource").count() == 1
        assert sorted(x.state for x in FileAsset.query.all()) == ["active", "deleted"]


def test_file_io_fail_isolated(env, monkeypatch):
    app, client, _, headers, uploads = env
    import file_service
    monkeypatch.setattr(file_service.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("isolated injected error")))
    response = client.post("/api/toolkits", data={"title": "fail", "description": "desc", "resource": (io.BytesIO(b"data"), "test.py")}, headers=headers["seller"])
    assert response.status_code == 503
    assert "isolated injected" not in str(response.json)
    with app.app_context():
        assert Toolkit.query.count() == FileReference.query.count() == 0
        assert all(x.state == "deleted" for x in FileAsset.query.all())
    assert not list((uploads / "resource").glob("*"))


def test_reject_private_cover_and_preserve_legacy_without_deletion(env):
    app, client, ids, headers, uploads = env
    stored = "a" * 32 + "_private.svg"
    public = "b" * 32 + "_legacy.svg"
    with app.app_context():
        private = FileAsset(stored_name=stored, owner_id=ids["seller"], purpose="asset", size=10, state="active", is_public=False)
        legacy = FileAsset(stored_name=public, owner_id=None, purpose="image", size=10, state="legacy", is_public=True)
        db.session.add_all([private, legacy])
        db.session.commit()
        legacy_id = legacy.id
    assert client.post("/api/toolkits", json={"title": "attack", "description": "desc", "cover": "/api/uploads/" + stored},
                       headers=headers["other"]).status_code == 403
    response = client.post("/api/toolkits", json={"title": "legacy", "description": "desc", "cover": "/api/uploads/" + public}, headers=headers["other"])
    assert response.status_code == 201, response.json
    (uploads / "image").mkdir(parents=True)
    (uploads / "image" / public).write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    assert client.get("/api/uploads/" + public).status_code == 200
    assert client.delete(f"/api/toolkits/{response.json['id']}", headers=headers["other"]).status_code == 200
    assert (uploads / "image" / public).exists()
    with app.app_context():
        assert db.session.get(FileAsset, legacy_id).owner_id is None
        assert db.session.get(FileAsset, legacy_id).state == "legacy"
    assert client.get("/api/uploads/" + "c" * 32 + "_unknown.svg").status_code == 404


def test_file_quota_and_skipped_does_not_reserve(env):
    app, client, _, headers, uploads = env
    app.config["FILE_USER_QUOTA"] = 5
    item = batch_item("full", resource="tiny.py")
    response = client.post("/api/batch/upload", data={"package": (package([item], {"tiny.py": b"12345"}), "p.zip")}, headers=headers["seller"])
    assert response.json["created"] == 1, response.json
    retry = client.post("/api/batch/upload", data={"package": (package([item], {"tiny.py": b"12345"}), "p.zip")}, headers=headers["seller"])
    assert retry.json["skipped"] == 1 and retry.json["failed"] == 0, retry.json
    over = client.post("/api/toolkits", data={"title": "over", "description": "desc", "resource": (io.BytesIO(b"1"), "more.py")}, headers=headers["seller"])
    assert over.status_code == 409, over.json
    with app.app_context():
        assert FileAsset.query.count() == 1 and FileReference.query.count() == 1


def test_edit_replaces_resource_after_commit_and_keeps_author_ownership(env):
    app, client, ids, headers, uploads = env
    toolkit = make_toolkit(env, resource=b"old")
    with app.app_context():
        old = db.session.get(Toolkit, toolkit["id"]).resource_path
    response = client.put(f"/api/toolkits/{toolkit['id']}", data={"title": "edited", "description": "new desc",
                          "lessons": json.dumps([{ "title": "new", "content": "new body"}]),
                          "resource": (io.BytesIO(b"replacement"), "new.py")}, headers=headers["seller"])
    assert response.status_code == 200, response.json
    assert not (uploads / "resource" / old).exists()
    download = client.get(f"/api/toolkits/{toolkit['id']}/download", headers=headers["admin"])
    assert download.data == b"replacement"
    download.close()
    # Admin editing text must not acquire/delete the author's resource ownership.
    edit = client.put(f"/api/toolkits/{toolkit['id']}", json={"title": "admin edited", "description": "desc", "lessons": []}, headers=headers["admin"])
    assert edit.status_code == 200, edit.json
    with app.app_context():
        active = FileAsset.query.filter_by(state="active").one()
        assert active.owner_id == ids["seller"]
        assert FileReference.query.filter_by(usage="resource").count() == 1


def test_pending_purchase_history_resolves_without_second_charge(env):
    app, client, ids, headers, _ = env
    toolkit = make_toolkit(env)
    oid = buy(env, toolkit)
    with app.app_context():
        pending = Order(order_no="historic-with-purchase", buyer_id=ids["buyer"], seller_id=ids["seller"],
                        toolkit_id=toolkit["id"], amount=10, commission=2, status="pending")
        db.session.add(pending)
        db.session.commit()
        pid = pending.id
    response = client.post(f"/api/orders/{pid}/pay", headers=headers["buyer"])
    assert response.status_code == 409 and response.json["code"] == "ALREADY_PURCHASED"
    with app.app_context():
        assert db.session.get(Order, pid).status == "cancelled"
        assert db.session.get(User, ids["buyer"]).balance == Decimal("190")
        assert WalletTransaction.query.count() == 2


def test_wallet_net_income_excludes_positive_buyer_refund(env):
    app, client, ids, headers, _ = env
    toolkit = make_toolkit(env)
    oid = buy(env, toolkit)
    with app.app_context():
        db.session.add_all([WalletTransaction(user_id=ids["seller"], type="refund", amount=-1, balance_after=1, order_id=oid),
                            WalletTransaction(user_id=ids["buyer"], type="refund", amount=10, balance_after=200, order_id=oid)])
        db.session.commit()
    seller = client.get("/api/wallet", headers=headers["seller"]).json
    buyer = client.get("/api/wallet", headers=headers["buyer"]).json
    assert seller["totalIncome"] == 2 and seller["netIncome"] == 1
    assert buyer["totalIncome"] == buyer["netIncome"] == 0


def test_list_uses_sql_counts_and_no_lesson_body_n_plus_one(env):
    app, client, _, _, _ = env
    for price in range(5):
        make_toolkit(env, price=price)
    statements = []
    with app.app_context():
        engine = db.engine
    def collect(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", collect)
    try:
        response = client.get("/api/toolkits")
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert response.status_code == 200 and all(x["lessonCount"] == 2 for x in response.json["items"])
    assert len(statements) == 2
    assert all("lesson.content" not in statement for statement in statements)


def test_review_order_locks_use_sorted_existing_primary_keys(env):
    app, client, ids, headers, _ = env
    toolkit = make_toolkit(env, price=12)
    created = client.post("/api/orders", json={"toolkitId": toolkit["id"]}, headers=headers["buyer"]).json
    with app.app_context():
        historical = Order(order_no="point-lock-history", buyer_id=ids["buyer"], seller_id=ids["seller"],
                           toolkit_id=toolkit["id"], amount=12, commission=Decimal("2.40"), status="pending")
        db.session.add(historical)
        db.session.commit()
        historical_id = historical.id
    locks = []
    def observe(execution):
        statement = execution.statement
        if execution.is_select and statement._for_update_arg is not None and any(
                getattr(table, "name", None) == "order" for table in statement.get_final_froms()):
            locks.append([(criterion.left.name, criterion.right.value) for criterion in statement._where_criteria])
    event.listen(Session, "do_orm_execute", observe)
    try:
        response = client.post(f"/api/orders/{created['id']}/pay", headers=headers["buyer"])
    finally:
        event.remove(Session, "do_orm_execute", observe)
    assert response.status_code == 200, response.json
    assert locks == [[("id", created["id"])], [("id", historical_id)]]
    with app.app_context():
        assert db.session.get(Order, historical_id).status == "cancelled"
        assert Purchase.query.count() == 1 and WalletTransaction.query.count() == 2


def test_review_child_locks_skip_missing_keys_and_lock_existing_primary_keys(env):
    from datetime import timedelta
    from flask_jwt_extended import decode_token
    from models import utcnow
    from money_service import lock_users
    from security import APIError, Identity, assert_identity
    app, client, ids, headers, _ = env
    toolkit = make_toolkit(env, price=12)
    locks = {"purchase": [], "wallet_receipt": [], "revoked_token": []}
    def observe(execution):
        statement = execution.statement
        if execution.is_select and statement._for_update_arg is not None:
            for table in statement.get_final_froms():
                if getattr(table, "name", None) in locks:
                    locks[table.name].append([(criterion.left.name, criterion.right.value)
                                              for criterion in statement._where_criteria])
    event.listen(Session, "do_orm_execute", observe)
    try:
        wallet_headers = {**headers["buyer"], "Idempotency-Key": "child-point-key"}
        first = client.post("/api/wallet/recharge", json={"amount": "40"}, headers=wallet_headers)
        assert first.status_code == 200
        assert all(not rows for rows in locks.values())
        with app.app_context():
            receipt_id = WalletReceipt.query.one().id
        assert client.post("/api/wallet/recharge", json={"amount": "40"}, headers=wallet_headers).json == first.json
        assert locks["wallet_receipt"] == [[("id", receipt_id)]]
        order = client.post("/api/orders", json={"toolkitId": toolkit["id"]}, headers=headers["buyer"])
        assert order.status_code == 201
        assert client.post(f"/api/orders/{order.json['id']}/pay", headers=headers["buyer"]).status_code == 200
        assert locks["purchase"] == [] and locks["revoked_token"] == []
        with app.app_context():
            purchase_id = Purchase.query.one().id
        assert client.post("/api/orders", json={"toolkitId": toolkit["id"]}, headers=headers["buyer"]).status_code == 409
        assert locks["purchase"] == [[("id", purchase_id)]]
        assert client.post("/api/auth/logout", headers=headers["buyer"]).status_code == 200
        assert locks["revoked_token"] == []
        with app.app_context():
            jti = decode_token(headers["buyer"]["Authorization"].split(" ", 1)[1])["jti"]
            identity = Identity(ids["buyer"], 0, jti, utcnow() + timedelta(minutes=10))
            with Session(db.engine) as session, session.begin():
                user = lock_users(session, [ids["buyer"]])[ids["buyer"]]
                with pytest.raises(APIError) as failure:
                    assert_identity(session, user, identity)
                assert failure.value.status == 401
        assert locks["revoked_token"] == [[("jti", jti)]]
    finally:
        event.remove(Session, "do_orm_execute", observe)


def test_review_create_order_explicitly_locks_both_fk_users(env, monkeypatch):
    _, client, ids, headers, _ = env
    import money_service
    toolkit = make_toolkit(env, price=12)
    calls = []
    original = money_service.lock_users
    def record(session, uids):
        result = original(session, uids)
        calls.append((sorted(set(uids)), list(result)))
        return result
    monkeypatch.setattr(money_service, "lock_users", record)
    response = client.post("/api/orders", json={"toolkitId": toolkit["id"]}, headers=headers["buyer"])
    assert response.status_code == 201, response.json
    assert calls == [([ids["seller"], ids["buyer"]], [ids["seller"], ids["buyer"]])]


def test_review_partial_and_original_lesson_edits_preserve_ids_progress(env):
    app, client, ids, headers, _ = env
    toolkit = make_toolkit(env, price=0)
    lid = toolkit["lessons"][0]["id"]
    assert client.post(f"/api/lessons/{lid}/complete", headers=headers["buyer"]).status_code == 200
    partial = client.put(f"/api/toolkits/{toolkit['id']}", json={"title": "title only"}, headers=headers["seller"])
    assert partial.status_code == 200, partial.json
    assert [x["id"] for x in partial.json["lessons"]] == [x["id"] for x in toolkit["lessons"]]
    detail = client.get(f"/api/toolkits/{toolkit['id']}", headers=headers["seller"]).json
    payload = {k: detail[k] for k in ["title", "description", "summary", "category", "level", "price", "cover", "lessons"]}
    payload["tags"] = ",".join(detail["tags"])
    same = client.put(f"/api/toolkits/{toolkit['id']}", json=payload, headers=headers["seller"])
    assert same.status_code == 200, same.json
    assert [x["id"] for x in same.json["lessons"]] == [x["id"] for x in toolkit["lessons"]]
    assert client.get(f"/api/me/lesson-progress?toolkit_id={toolkit['id']}", headers=headers["buyer"]).json == [lid]
    with app.app_context():
        assert LessonProgress.query.filter_by(user_id=ids["buyer"], lesson_id=lid).count() == 1
    # Actual deletion removes only that lesson's progress and yields a clear 404.
    payload["lessons"] = [payload["lessons"][1]]
    removed = client.put(f"/api/toolkits/{toolkit['id']}", json=payload, headers=headers["seller"])
    assert removed.status_code == 200
    assert client.post(f"/api/lessons/{lid}/complete", headers=headers["buyer"]).status_code == 404


def test_review_admin_private_image_roundtrip_is_same_lesson_only(env):
    app, client, _, headers, _ = env
    item = batch_item("private roundtrip")
    item["lessons"][0]["image"] = "first.png"
    item["lessons"][1]["image"] = "second.png"
    result = client.post("/api/batch/upload", data={"package": (package([item], {"first.png": png_bytes(), "second.png": png_bytes()}), "p.zip")}, headers=headers["seller"])
    tid = result.json["results"][0]["toolkitId"]
    detail = client.get(f"/api/toolkits/{tid}", headers=headers["admin"]).json
    payload = {k: detail[k] for k in ["title", "description", "summary", "category", "level", "price", "cover", "lessons"]}
    payload["tags"] = ",".join(detail["tags"])
    unchanged = client.put(f"/api/toolkits/{tid}", json=payload, headers=headers["admin"])
    assert unchanged.status_code == 200, unchanged.json
    assert [x["id"] for x in unchanged.json["lessons"]] == [x["id"] for x in detail["lessons"]]
    with app.app_context():
        for asset in FileAsset.query.all():
            asset.owner_id, asset.state = None, "legacy"
        db.session.commit()
    assert client.put(f"/api/toolkits/{tid}", json=payload, headers=headers["admin"]).status_code == 200
    # A different Lesson cannot inherit preservation from a toolkit-wide set.
    payload["lessons"][0]["image"] = payload["lessons"][1]["image"]
    denied = client.put(f"/api/toolkits/{tid}", json=payload, headers=headers["admin"])
    assert denied.status_code == 403, denied.json
    foreign = client.post("/api/toolkits", json={"title": "foreign", "description": "desc", "lessons": [
        {"title": "new", "content": "body", "image": payload["lessons"][1]["image"]}]}, headers=headers["admin"])
    assert foreign.status_code == 403


def test_review_recover_pending_active_reclaim_and_protect_refs_owner(env, monkeypatch):
    app, client, _, headers, uploads = env
    from datetime import timedelta
    from models import utcnow
    from file_service import recover_file_operations
    toolkit = make_toolkit(env, price=0, resource=b"remove after commit")
    with app.app_context():
        asset = FileAsset.query.one()
        aid, stored = asset.id, asset.stored_name
    monkeypatch.setattr("app.reclaim_asset", lambda *args: (_ for _ in ()).throw(RuntimeError("postcommit failure")))
    assert client.delete(f"/api/toolkits/{toolkit['id']}", headers=headers["seller"]).status_code == 200
    assert (uploads / "resource" / stored).exists()
    live = make_toolkit(env, price=1, resource=b"still referenced")
    unknown_stored = "d" * 32 + "_unknown.py"
    (uploads / "resource" / unknown_stored).write_bytes(b"unknown owner")
    with app.app_context():
        old = utcnow() - timedelta(days=1)
        unknown = FileAsset(stored_name=unknown_stored, owner_id=None, purpose="resource", size=13, state="active", is_public=False, created_at=old)
        db.session.add(unknown)
        db.session.flush()
        live_asset = FileAsset.query.filter(FileAsset.id != aid, FileAsset.owner_id.is_not(None)).one()
        for candidate in [live_asset, unknown]:
            db.session.add(FileOperation(id=("e" if candidate is live_asset else "f") * 32,
                asset_id=candidate.id, action="reclaim", status="pending", created_at=old))
        for operation in FileOperation.query.filter_by(action="reclaim").all():
            operation.created_at = old
        db.session.commit()
        assert db.session.get(FileAsset, aid).state == "active"
        report = recover_file_operations()
        assert report == {"reclaimed": 1, "retained": 2}
        db.session.expire_all()
        assert db.session.get(FileAsset, aid).state == "deleted"
        assert not FileOperation.query.filter_by(asset_id=aid, action="reclaim", status="pending").count()
        assert live_asset.state == "active" and unknown.owner_id is None
    assert not (uploads / "resource" / stored).exists()
    assert (uploads / "resource" / unknown_stored).exists()
    download = client.get(f"/api/toolkits/{live['id']}/download", headers=headers["seller"])
    assert download.data == b"still referenced"
    download.close()


def test_review_dotenv_effective_production_and_injected_test_no_read(monkeypatch, tmp_path):
    from config import build_config
    import dotenv
    for key in list(os.environ):
        monkeypatch.delenv(key)
    monkeypatch.setenv("LOAD_DOTENV", "1")
    base = {"APP_ENV": "production", "JWT_SECRET_KEY": "x" * 64, "DATABASE_URL": "sqlite:///:memory:"}
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *args: {**base, "DEBUG": "1", "WALLET_MODE": "simulation"})
    with pytest.raises(ValueError, match="Production forbids"):
        build_config()
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *args: base)
    assert build_config()["APP_ENV"] == "production"
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *args: {**base, "APP_ENV": "invalid"})
    with pytest.raises(ValueError, match="APP_ENV"):
        build_config()
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *args: (_ for _ in ()).throw(AssertionError("must not read dotenv")))
    config = build_config({"APP_ENV": "test", "TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                           "UPLOAD_FOLDER": str(tmp_path), "JWT_SECRET_KEY": "test-only-key-at-least-32-characters", "LOAD_DOTENV": True})
    assert config["APP_ENV"] == "test"


def test_review_chinese_image_resource_upload_roundtrip(env):
    app, client, _, headers, uploads = env
    item = batch_item("中文文件", cover="covers/封面.png", resource="resources/脚本.py")
    item["lessons"][0]["image"] = "images/步骤.png"
    response = client.post("/api/batch/upload", data={"package": (package([item], {
        "covers/封面.png": png_bytes(), "images/步骤.png": png_bytes(), "resources/脚本.py": b"print('safe attachment')"}), "中文包.zip")}, headers=headers["seller"])
    assert response.status_code == 200 and response.json["created"] == 1, response.json
    tid = response.json["results"][0]["toolkitId"]
    detail = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert detail["cover"].endswith(".png")
    assert client.get(detail["cover"]).status_code == 200
    assert client.get(detail["lessons"][0]["image"]).status_code == 200
    resource = client.get(f"/api/toolkits/{tid}/download", headers=headers["seller"])
    assert resource.data == b"print('safe attachment')"
    resource.close()
    single = client.post("/api/toolkits", data={"title": "单项中文", "description": "desc", "resource": (io.BytesIO(b"single"), "脚本.py")}, headers=headers["seller"])
    assert single.status_code == 201 and single.json["resourceName"].endswith(".py"), single.json
    # Both single and batch call the same image/filename preparation service.
    from file_service import UploadContext
    from security import Identity
    from models import utcnow
    from datetime import timedelta
    from app import _persist_item
    with app.app_context():
        from flask_jwt_extended import decode_token
        claims = decode_token(headers["seller"]["Authorization"][7:])
        identity = Identity(int(claims["sub"]), claims["token_version"], claims["jti"], utcnow() + timedelta(minutes=5))
        image = UploadContext(identity).prepare("image", "封面.png", io.BytesIO(png_bytes()), len(png_bytes()))
        assert image["stored_name"].endswith("_file.png")


@pytest.mark.parametrize("name", ["../脚本.py", "目录/脚本.py", "目录\\脚本.py", "脚本\x00.py", "a" * 201 + ".py", "脚本.exe.txt"])
def test_review_unsafe_names_remain_rejected(env, name):
    _, client, _, headers, _ = env
    # NUL is passed directly to the common service; HTTP stacks can reject it earlier.
    from file_service import _filename
    if "\x00" in name or "\\" in name:
        # Werkzeug can normalize Windows separators before FileStorage; the
        # shared service itself must still reject raw path/NUL names.
        with pytest.raises(ValueError):
            _filename(name)
        return
    response = client.post("/api/toolkits", data={"title": "bad name", "description": "desc", "resource": (io.BytesIO(b"data"), name)}, headers=headers["seller"])
    assert response.status_code == 400, response.json


def test_persistent_rate_bounded_and_ttl(env):
    app, client, _, _, _ = env
    for index in range(8):
        assert client.post("/api/auth/login", json={"username": "buyer", "password": "wrong123"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "buyer", "password": "wrong123"}).status_code == 429
    with app.app_context():
        assert RateLimitBucket.query.count() == 4
        from models import utcnow
        from datetime import timedelta
        for row in RateLimitBucket.query.all():
            row.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert client.post("/api/auth/login", json={"username": "buyer", "password": "wrong123"}).status_code == 401
    app.config["RATE_LIMIT_CAPACITY"] = 4
    assert client.post("/api/auth/login", json={"username": "unknown", "password": "wrong123"}).status_code == 429
    with app.app_context():
        assert RateLimitBucket.query.count() == 4
