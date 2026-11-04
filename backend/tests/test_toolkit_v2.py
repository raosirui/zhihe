"""Regressions for the v2 toolkit features: async batch jobs, update mode,
multi-attachment resources, drafts, and buyer ratings."""
import io
import json
import zipfile
import pytest
from flask_jwt_extended import create_access_token
from PIL import Image
from werkzeug.security import generate_password_hash
from app import create_app, db, User, Toolkit, FileAsset, FileReference, BatchJob, ToolkitRating


@pytest.fixture
def env(tmp_path):
    app = create_app({"APP_ENV": "test", "TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                      "UPLOAD_FOLDER": str(tmp_path / "uploads"),
                      "JWT_SECRET_KEY": "isolated-test-secret-at-least-32-characters",
                      "WALLET_MODE": "simulation"})
    with app.app_context():
        db.create_all()
        users = [User(username=name, nickname=name, role=role, balance=balance,
                      password_hash=generate_password_hash(password)) for name, role, balance, password in [
                      ("admin", "admin", 0, "Admin123"), ("seller", "user", 0, "Seller123"),
                      ("buyer", "user", 200, "Buyer123"), ("other", "user", 0, "Other123")]]
        db.session.add_all(users)
        db.session.commit()
        ids = {x.username: x.id for x in users}
        headers = {x.username: {"Authorization": "Bearer " + create_access_token(identity=str(x.id),
                    additional_claims={"token_version": x.token_version})} for x in users}
    yield app, app.test_client(), ids, headers, tmp_path / "uploads"
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


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


def drain(app, client, headers):
    """Run the worker loop once synchronously; return the first job report."""
    import batch_worker
    job_id = None
    with app.app_context():
        while True:
            claimed = batch_worker.claim("test-worker")
            if claimed is None:
                break
            job_id = claimed
            batch_worker.load_and_process(app, claimed)
    assert job_id is not None
    return client.get(f"/api/batch/jobs/{job_id}", headers=headers).json


def upload_batch(env, items, assets=None, form=None):
    app, client, _, headers, _ = env
    data = {"package": (package(items, assets), "p.zip")}
    data.update(form or {})
    response = client.post("/api/batch/upload", data=data, headers=headers["seller"])
    assert response.status_code == 202, response.json
    return response.json["jobId"], drain(app, client, headers["seller"])


def test_async_batch_enqueues_and_reports(env):
    app, client, _, headers, uploads = env
    job_id, report = upload_batch(env, [batch_item("alpha"), batch_item("beta")])
    assert report["status"] == "done" and report["total"] == 2
    assert report["created"] == 2 and report["processed"] == 2
    assert all(x["status"] == "created" for x in report["results"])
    assert not (uploads / ".batches" / f"{job_id}.zip").exists()
    with app.app_context():
        job = db.session.get(BatchJob, job_id)
        assert job.owner_id and job.status == "done"
    listed = client.get("/api/batch/jobs", headers=headers["seller"]).json
    assert listed[0]["jobId"] == job_id
    # Other users cannot read someone else's job.
    assert client.get(f"/api/batch/jobs/{job_id}", headers=headers["other"]).status_code == 404


def test_batch_update_mode_overwrites_same_title(env):
    app, client, _, headers, uploads = env
    upload_batch(env, [batch_item("alpha", tags="v1", resource="one.bin")], {"one.bin": b"first attachment"})
    tid = [x["id"] for x in client.get("/api/me/toolkits", headers=headers["seller"]).json][0]
    job_id, report = upload_batch(env, [batch_item("alpha", tags="v2", resource="two.bin")],
                                  {"two.bin": b"second attachment"}, form={"mode": "update"})
    assert report["updated"] == 1 and report["created"] == 0
    assert report["results"][0]["toolkitId"] == tid
    detail = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert detail["tags"] == ["v2"]
    assert len(detail["resources"]) == 1
    attachment = client.get(f"/api/toolkits/{tid}/download", headers=headers["seller"])
    assert attachment.data == b"second attachment"


def test_batch_publish_zero_creates_drafts(env):
    app, client, _, headers, _ = env
    _, report = upload_batch(env, [batch_item("draft one")], form={"publish": "0"})
    assert report["created"] == 1
    tid = report["results"][0]["toolkitId"]
    assert client.get(f"/api/toolkits/{tid}").status_code == 404
    assert client.get("/api/toolkits?q=draft+one").json["total"] == 0
    detail = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert detail["status"] == "draft"
    assert client.get(detail["cover"]).status_code == 200 if detail["cover"] else True
    # Author publishes; anonymous listings now include it.
    assert client.put(f"/api/toolkits/{tid}/status", json={"status": "published"},
                      headers=headers["seller"]).json["status"] == "published"
    assert client.get("/api/toolkits?q=draft+one").json["total"] == 1


def test_draft_status_matrix(env):
    app, client, _, headers, _ = env
    created = client.post("/api/toolkits", json={"title": "draft flow", "description": "d", "status": "draft"},
                          headers=headers["seller"])
    assert created.status_code == 201 and created.json["status"] == "draft"
    tid = created.json["id"]
    # Authors cannot resurrect offline toolkits or jump offline->draft.
    assert client.put(f"/api/toolkits/{tid}/status", json={"status": "offline"}, headers=headers["other"]).status_code == 403
    assert client.put(f"/api/toolkits/{tid}/status", json={"status": "published"}, headers=headers["seller"]).status_code == 200
    assert client.put(f"/api/toolkits/{tid}/status", json={"status": "offline"}, headers=headers["seller"]).status_code == 200
    assert client.put(f"/api/toolkits/{tid}/status", json={"status": "published"}, headers=headers["seller"]).status_code == 403
    assert client.put(f"/api/toolkits/{tid}/status", json={"status": "published"}, headers=headers["admin"]).status_code == 200


def test_multi_attachment_resources_roundtrip(env):
    app, client, _, headers, _ = env
    _, report = upload_batch(env, [batch_item("multi", resources=["a.bin", "b.bin", "c.bin"])],
                             {"a.bin": b"AAA", "b.bin": b"BBB", "c.bin": b"CCC"})
    tid = report["results"][0]["toolkitId"]
    detail = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    names = [x["name"] for x in detail["resources"]]
    assert names == ["a.bin", "b.bin", "c.bin"]
    assert all(not x["locked"] for x in detail["resources"])  # managers can download their own
    urls = [x["downloadUrl"] for x in detail["resources"]]
    assert client.get(urls[1], headers=headers["seller"]).data == b"BBB"
    # Deleting the toolkit reclaims every attachment.
    assert client.delete(f"/api/toolkits/{tid}", headers=headers["seller"]).json["mode"] == "deleted"
    assert client.get(urls[1], headers=headers["seller"]).status_code == 404


def test_single_upload_multiple_multipart_files(env):
    app, client, _, headers, _ = env
    data = {"title": "two files", "description": "d",
            "lessons": json.dumps([{"title": "s1", "content": "body", "preview": True}]),
            "resource": [(io.BytesIO(b"X1"), "x1.txt"), (io.BytesIO(b"X2"), "x2.txt")]}
    created = client.post("/api/toolkits", data=data, headers=headers["seller"])
    assert created.status_code == 201, created.json
    tid = created.json["id"]
    detail = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert [x["name"] for x in detail["resources"]] == ["x1.txt", "x2.txt"]
    downloads = [client.get(f"/api/toolkits/{tid}/resources/{x['refId']}/download", headers=headers["seller"])
                 for x in detail["resources"]]
    assert [d.data for d in downloads] == [b"X1", b"X2"]
    # Editing without new files preserves both attachments and their bindings.
    payload = {k: detail[k] for k in ["title", "description", "summary", "category", "level", "price", "cover"]}
    payload["tags"] = ",".join(detail["tags"])
    edited = client.put(f"/api/toolkits/{tid}", json=payload, headers=headers["seller"])
    assert edited.status_code == 200, edited.json
    detail2 = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert [x["name"] for x in detail2["resources"]] == ["x1.txt", "x2.txt"]
    assert client.get(f"/api/toolkits/{tid}/resources/{detail2['resources'][0]['refId']}/download",
                      headers=headers["seller"]).data == b"X1"
    with app.app_context():
        assert FileReference.query.filter_by(usage="resource", toolkit_id=tid).count() == 2
        assert all(x.state == "active" for x in FileAsset.query.all())


def test_buyer_rating_flow_and_permissions(env):
    app, client, _, headers, _ = env
    data = {"title": "rated", "description": "d", "price": "10",
            "lessons": json.dumps([{"title": "p", "content": "body", "preview": True}])}
    tid = client.post("/api/toolkits", json=data, headers=headers["seller"]).json["id"]
    assert client.put(f"/api/toolkits/{tid}/rating", json={"score": 5}, headers=headers["buyer"]).status_code == 403
    order = client.post("/api/orders", json={"toolkitId": tid}, headers=headers["buyer"]).json["id"]
    client.post(f"/api/orders/{order}/pay", headers=headers["buyer"])
    assert client.put(f"/api/toolkits/{tid}/rating", json={"score": 0}, headers=headers["buyer"]).status_code == 400
    first = client.put(f"/api/toolkits/{tid}/rating", json={"score": 4}, headers=headers["buyer"])
    assert first.status_code == 200 and first.json == {"rating": 4.0, "ratingCount": 1, "myScore": 4}
    detail = client.get(f"/api/toolkits/{tid}").json
    assert detail["rating"] == 4.0 and detail["ratingCount"] == 1
    # Re-rating updates instead of accumulating.
    assert client.put(f"/api/toolkits/{tid}/rating", json={"score": 2},
                      headers=headers["buyer"]).json["rating"] == 2.0
    with app.app_context():
        assert ToolkitRating.query.count() == 1
