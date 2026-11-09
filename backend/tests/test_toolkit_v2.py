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


def test_batch_put_verification_failure_reclaims_remote_package(env, monkeypatch):
    import batch_worker

    app, client, _, headers, uploads = env
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    remote = {}
    deleted = []

    def fail_after_put(key, path, size, digest):
        remote[key] = digest
        raise OSError("HEAD failed after the object was stored")

    def delete_remote(key):
        deleted.append(key)
        remote.pop(key, None)

    monkeypatch.setattr(batch_worker.storage, "put_file", fail_after_put)
    monkeypatch.setattr(batch_worker.storage, "delete_file", delete_remote)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("copy")]), "p.zip")}, headers=headers["seller"])
    assert response.status_code == 503
    assert len(deleted) == 1 and not remote
    assert not list((uploads / ".batches").glob("*.zip"))
    with app.app_context():
        assert db.session.query(BatchJob).count() == 0


def _simulate_lost_batch_commit_ack(monkeypatch):
    """The row commits, but the transaction context reports a lost connection."""
    import batch_worker
    from contextlib import contextmanager
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    class LostCommitAckSession(Session):
        @contextmanager
        def begin(self, *args, **kwargs):
            with super().begin(*args, **kwargs):
                yield self
            raise OperationalError("COMMIT", None, OSError("connection lost after commit"))

    monkeypatch.setattr(batch_worker, "Session", LostCommitAckSession)


def test_batch_ambiguous_commit_keeps_verified_remote_package(env, monkeypatch, caplog):
    import batch_worker
    from pathlib import Path
    import hashlib

    app, client, _, headers, uploads = env
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    remote = {}
    deleted = []

    def put_remote(key, path, size, digest):
        content = Path(path).read_bytes()
        assert len(content) == size and hashlib.sha256(content).hexdigest() == digest
        remote[key] = content

    with monkeypatch.context() as patch:
        patch.setattr(batch_worker.storage, "put_file", put_remote)
        patch.setattr(batch_worker.storage, "delete_file", deleted.append)
        _simulate_lost_batch_commit_ack(patch)
        response = client.post("/api/batch/upload", data={
            "package": (package([batch_item("committed")]), "p.zip")}, headers=headers["seller"])

    assert response.status_code == 503
    assert len(remote) == 1 and not deleted
    assert not list((uploads / ".batches").glob("*.zip"))
    with app.app_context():
        job = db.session.query(BatchJob).one()
        assert job.status == "queued"
        assert job.package_object_key in remote and job.package_path is None
        assert job.package_sha256 == hashlib.sha256(remote[job.package_object_key]).hexdigest()
        assert any(job.id in record.getMessage() and job.package_object_key in record.getMessage()
                   and "reconciliation" in record.getMessage() for record in caplog.records)


def test_batch_ambiguous_commit_keeps_local_staging_until_worker(env, monkeypatch, caplog):
    app, client, _, headers, uploads = env
    app.config["FILE_STORAGE_BACKEND"] = "local"
    with monkeypatch.context() as patch:
        _simulate_lost_batch_commit_ack(patch)
        response = client.post("/api/batch/upload", data={
            "package": (package([batch_item("local committed")]), "p.zip")}, headers=headers["seller"])

    assert response.status_code == 503
    with app.app_context():
        job = db.session.query(BatchJob).one()
        staged = uploads / ".batches" / f"{job.id}.zip"
        assert job.status == "queued" and job.package_path == str(staged)
        assert staged.exists()
        assert any(job.id in record.getMessage() and "reconciliation" in record.getMessage()
                   for record in caplog.records)
    assert drain(app, client, headers["seller"])["status"] == "done"
    assert not staged.exists()


@pytest.mark.parametrize("backend", ["local", "s3"])
def test_batch_failure_before_commit_cleans_unqueued_package(env, monkeypatch, backend):
    import batch_worker
    from sqlalchemy.exc import OperationalError

    app, client, _, headers, uploads = env
    app.config["FILE_STORAGE_BACKEND"] = backend
    remote = {}
    deleted = []

    def put_remote(key, path, size, digest):
        remote[key] = digest

    def delete_remote(key):
        deleted.append(key)
        remote.pop(key, None)

    def fail_before_commit(*args, **kwargs):
        raise OperationalError("SELECT", None, OSError("database unavailable"))

    monkeypatch.setattr(batch_worker.storage, "put_file", put_remote)
    monkeypatch.setattr(batch_worker.storage, "delete_file", delete_remote)
    monkeypatch.setattr(batch_worker.money, "lock_users", fail_before_commit)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("not queued")]), "p.zip")}, headers=headers["seller"])

    assert response.status_code == 503
    assert not list((uploads / ".batches").glob("*.zip"))
    assert len(deleted) == (1 if backend == "s3" else 0)
    assert not remote
    with app.app_context():
        assert db.session.query(BatchJob).count() == 0


def _mock_batch_object_store(monkeypatch):
    import batch_worker
    from pathlib import Path
    import hashlib

    remote, deleted = {}, []

    def put_remote(key, path, size, digest):
        data = Path(path).read_bytes()
        assert size == len(data) and hashlib.sha256(data).hexdigest() == digest
        remote[key] = data

    def read_remote(key, output, size, digest, maximum):
        data = remote[key]
        assert size == len(data) and size <= maximum
        assert hashlib.sha256(data).hexdigest() == digest
        output.write(data)

    def delete_remote(key):
        deleted.append(key)
        remote.pop(key, None)

    monkeypatch.setattr(batch_worker.storage, "put_file", put_remote)
    monkeypatch.setattr(batch_worker.storage, "read_to_file", read_remote)
    monkeypatch.setattr(batch_worker.storage, "delete_file", delete_remote)
    return remote, deleted


@pytest.mark.parametrize("fault", ["read", "set_total"])
def test_batch_pre_item_transient_error_retries_without_losing_s3_input(env, monkeypatch, fault):
    import batch_worker
    from sqlalchemy.exc import OperationalError

    app, client, _, headers, uploads = env
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    remote, deleted = _mock_batch_object_store(monkeypatch)
    target = batch_worker.storage.read_to_file if fault == "read" else batch_worker._set_total
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("temporary outage", None, OSError("connection interrupted"))
        return target(*args, **kwargs)

    monkeypatch.setattr(batch_worker.storage if fault == "read" else batch_worker,
                        "read_to_file" if fault == "read" else "_set_total", fail_once)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("retry once")]), "p.zip")}, headers=headers["seller"])
    assert response.status_code == 202
    job_id = response.json["jobId"]
    with app.app_context():
        assert batch_worker.claim("worker-one") == job_id
        batch_worker.load_and_process(app, job_id)
        job = db.session.get(BatchJob, job_id)
        key = job.package_object_key
        assert job.status == "queued" and "1/3" in job.error
        assert key in remote and deleted == []
        assert not list((uploads / ".batches").glob("*.zip"))
        assert batch_worker.claim("too-early") is None  # 30-second retry backoff.
        monkeypatch.setattr(batch_worker, "_RETRY_DELAY_SECONDS", 0)
        db.session.expire_all()
        assert batch_worker.claim("worker-two") == job_id
        batch_worker.load_and_process(app, job_id)
        db.session.expire_all()
        job = db.session.get(BatchJob, job_id)
        assert job.status == "done" and job.created == 1 and job.processed == 1
        assert job.package_object_key is None and job.package_sha256 is None
    assert calls == 2 and deleted == [key] and not remote
    assert len(client.get("/api/me/toolkits", headers=headers["seller"]).json) == 1


def test_batch_retries_are_bounded_without_replaying_items(env, monkeypatch):
    import batch_worker
    from sqlalchemy.exc import OperationalError

    app, client, _, headers, _ = env
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    remote, deleted = _mock_batch_object_store(monkeypatch)
    monkeypatch.setattr(batch_worker, "_RETRY_DELAY_SECONDS", 0)

    def fail_total(*args, **kwargs):
        raise OperationalError("UPDATE", None, OSError("connection interrupted"))

    monkeypatch.setattr(batch_worker, "_set_total", fail_total)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("never started")]), "p.zip")}, headers=headers["seller"])
    job_id = response.json["jobId"]
    with app.app_context():
        for attempt in range(1, batch_worker._RETRY_LIMIT + 1):
            assert batch_worker.claim(f"worker-{attempt}") == job_id
            batch_worker.load_and_process(app, job_id)
            db.session.expire_all()
            job = db.session.get(BatchJob, job_id)
            assert job.status == ("failed" if attempt == batch_worker._RETRY_LIMIT else "queued")
            assert job.package_object_key in remote and not deleted
        assert batch_worker.claim("another-worker") is None
        batch_worker.cleanup_completed_inputs(app, retry_after=0)
        assert job.package_object_key in remote and not deleted
    assert not client.get("/api/me/toolkits", headers=headers["seller"]).json


def test_batch_item_started_failure_is_not_replayed(env, monkeypatch):
    import batch_worker

    app, client, _, headers, _ = env
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    remote, deleted = _mock_batch_object_store(monkeypatch)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("one committed item")]), "p.zip")}, headers=headers["seller"])
    job_id = response.json["jobId"]
    def fail_tick(*args, **kwargs):
        raise OSError("progress lost")

    with app.app_context():
        assert batch_worker.claim("worker") == job_id
        with monkeypatch.context() as patch:
            patch.setattr(batch_worker, "_tick", fail_tick)
            batch_worker.load_and_process(app, job_id)
        db.session.expire_all()
        job = db.session.get(BatchJob, job_id)
        key = job.package_object_key
        assert job.status == "failed" and job.error == batch_worker._RETAINED_ERROR
        assert job.claimed_by == batch_worker._RETAINED_CLAIM
        assert key in remote and not deleted
        batch_worker.requeue_stale(-1)
        assert batch_worker.claim("another-worker") is None
        batch_worker.cleanup_completed_inputs(app, retry_after=0)
        assert key in remote and not deleted
    assert len(client.get("/api/me/toolkits", headers=headers["seller"]).json) == 1


def test_batch_ambiguous_item_commit_requires_reconciliation(env, monkeypatch):
    import batch_worker
    from sqlalchemy.exc import OperationalError

    app, client, _, headers, _ = env
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    remote, deleted = _mock_batch_object_store(monkeypatch)
    import_item = batch_worker._import_item

    def commit_then_lose_reply(*args, **kwargs):
        import_item(*args, **kwargs)
        raise OperationalError("COMMIT", None, OSError("connection lost after item commit"))

    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("committed before error")]), "p.zip")}, headers=headers["seller"])
    job_id = response.json["jobId"]
    with app.app_context():
        assert batch_worker.claim("worker") == job_id
        with monkeypatch.context() as patch:
            patch.setattr(batch_worker, "_import_item", commit_then_lose_reply)
            batch_worker.load_and_process(app, job_id)
        job = db.session.get(BatchJob, job_id)
        key = job.package_object_key
        assert job.status == "failed" and job.error == batch_worker._RETAINED_ERROR
        assert job.claimed_by == batch_worker._RETAINED_CLAIM
        assert job.processed == 0 and key in remote and not deleted
        assert batch_worker.claim("another-worker") is None
        batch_worker.cleanup_completed_inputs(app, retry_after=0)
        assert key in remote and not deleted
    assert len(client.get("/api/me/toolkits", headers=headers["seller"]).json) == 1


def test_batch_stale_item_started_job_requires_reconciliation(env, monkeypatch):
    import batch_worker

    app, client, _, headers, _ = env
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    remote, deleted = _mock_batch_object_store(monkeypatch)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("stale started")]), "p.zip")}, headers=headers["seller"])
    job_id = response.json["jobId"]
    with app.app_context():
        assert batch_worker.claim("worker") == job_id
        batch_worker._mark_items_started(job_id)
        batch_worker.requeue_stale(-1)
        job = db.session.get(BatchJob, job_id)
        assert job.status == "failed" and job.error == batch_worker._RETAINED_ERROR
        assert job.claimed_by == batch_worker._RETAINED_CLAIM
        assert job.package_object_key in remote and not deleted
        assert batch_worker.claim("another-worker") is None


def test_batch_repeated_stale_pre_item_claims_are_quarantined(env, monkeypatch):
    import batch_worker

    app, client, _, headers, uploads = env
    monkeypatch.setattr(batch_worker, "_RETRY_DELAY_SECONDS", 0)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("stale attempts")]), "p.zip")}, headers=headers["seller"])
    job_id = response.json["jobId"]
    staged = uploads / ".batches" / f"{job_id}.zip"
    with app.app_context():
        for attempt in range(1, batch_worker._RETRY_LIMIT + 1):
            assert batch_worker.claim(f"stale-worker-{attempt}") == job_id
            batch_worker.requeue_stale(-1)
            db.session.expire_all()
            job = db.session.get(BatchJob, job_id)
            assert staged.exists() and job.processed == 0
            if attempt < batch_worker._RETRY_LIMIT:
                assert job.status == "queued" and job.error.startswith(batch_worker._RETRY_NOTE)
                assert batch_worker._retry_count(job.error) == attempt
            else:
                assert job.status == "failed" and job.error == batch_worker._RETAINED_ERROR
                assert job.claimed_by == batch_worker._RETAINED_CLAIM
        assert batch_worker.claim("another-worker") is None


def test_all_invalid_batch_has_no_premature_no_replay_marker(env, monkeypatch):
    import batch_worker
    from sqlalchemy.exc import OperationalError

    app, client, _, headers, uploads = env
    response = client.post("/api/batch/upload", data={
        "package": (package([{"title": "", "description": "invalid item"}]), "p.zip")},
        headers=headers["seller"])
    job_id = response.json["jobId"]

    def lose_first_tick(*_args, **_kwargs):
        raise OperationalError("UPDATE", None, OSError("connection interrupted"))

    monkeypatch.setattr(batch_worker, "_tick", lose_first_tick)
    with app.app_context():
        assert batch_worker.claim("first-worker") == job_id
        batch_worker.load_and_process(app, job_id, "first-worker")
        db.session.expire_all()
        job = db.session.get(BatchJob, job_id)
        assert job.status == "queued" and job.error.startswith(batch_worker._RETRY_NOTE)
        assert job.claimed_by is None and job.processed == 0
        assert (uploads / ".batches" / f"{job_id}.zip").exists()



def test_batch_old_lease_cannot_start_import_after_reclaim(env, monkeypatch):
    import batch_worker

    app, client, _, headers, _ = env
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    remote, deleted = _mock_batch_object_store(monkeypatch)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("single import")]), "p.zip")}, headers=headers["seller"])
    job_id = response.json["jobId"]
    with app.app_context():
        assert batch_worker.claim("old-worker") == job_id
        monkeypatch.setattr(batch_worker, "_RETRY_DELAY_SECONDS", 0)
        batch_worker.requeue_stale(-1)
        assert batch_worker.claim("new-worker") == job_id
        assert batch_worker._finish(job_id, "done", "old-worker") is False
        with pytest.raises(RuntimeError, match="lost its worker claim"):
            batch_worker._set_total(job_id, 999, "old-worker")
        with pytest.raises(RuntimeError, match="active claim"):
            batch_worker._tick(job_id, "old-worker", failed=1)
        batch_worker.load_and_process(app, job_id, "old-worker")
        job = db.session.get(BatchJob, job_id)
        key = job.package_object_key
        assert job.status == "running" and job.claimed_by == "new-worker"
        assert key in remote and not deleted
        assert not client.get("/api/me/toolkits", headers=headers["seller"]).json
        batch_worker.load_and_process(app, job_id, "new-worker")
        db.session.expire_all()
        job = db.session.get(BatchJob, job_id)
        assert job.status == "done" and job.created == 1
    assert deleted == [key] and not remote
    assert len(client.get("/api/me/toolkits", headers=headers["seller"]).json) == 1


def test_batch_claim_heartbeat_renews_only_the_current_worker(env):
    import batch_worker
    from datetime import timedelta
    from models import utcnow

    app, client, _, headers, _ = env
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("lease renewal")]), "p.zip")}, headers=headers["seller"])
    job_id = response.json["jobId"]

    class OnePulse:
        def __init__(self):
            self.waits = 0

        def wait(self, interval):
            assert interval > 0
            self.waits += 1
            return self.waits > 1

    with app.app_context():
        assert batch_worker.claim("owner-worker") == job_id
        job = db.session.get(BatchJob, job_id)
        stale = utcnow() - timedelta(minutes=1)
        job.claimed_at = stale
        db.session.commit()

        batch_worker._renew_claim(app, job_id, "owner-worker", OnePulse())
        db.session.expire_all()
        job = db.session.get(BatchJob, job_id)
        assert job.claimed_at > stale
        batch_worker.requeue_stale(30)
        db.session.expire_all()
        assert db.session.get(BatchJob, job_id).status == "running"

        batch_worker._mark_items_started(job_id, "owner-worker")
        job = db.session.get(BatchJob, job_id)
        job.claimed_at = stale
        db.session.commit()
        batch_worker._renew_claim(app, job_id, "owner-worker", OnePulse())
        db.session.expire_all()
        job = db.session.get(BatchJob, job_id)
        assert job.claimed_by == "items:owner-worker" and job.claimed_at > stale

        job.claimed_at = stale
        job.claimed_by = "items:different-worker"
        db.session.commit()
        batch_worker._renew_claim(app, job_id, "owner-worker", OnePulse())
        db.session.expire_all()
        job = db.session.get(BatchJob, job_id)
        assert job.claimed_at == stale and job.claimed_by == "items:different-worker"



def test_batch_local_transient_failure_keeps_staging_for_retry(env, monkeypatch):
    import batch_worker
    from sqlalchemy.exc import OperationalError

    app, client, _, headers, uploads = env
    app.config["FILE_STORAGE_BACKEND"] = "local"
    monkeypatch.setattr(batch_worker, "_RETRY_DELAY_SECONDS", 0)
    set_total = batch_worker._set_total
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError("UPDATE", None, OSError("connection interrupted"))
        set_total(*args, **kwargs)

    monkeypatch.setattr(batch_worker, "_set_total", fail_once)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("local retry")]), "p.zip")}, headers=headers["seller"])
    job_id = response.json["jobId"]
    staged = uploads / ".batches" / f"{job_id}.zip"
    with app.app_context():
        assert batch_worker.claim("first-worker") == job_id
        batch_worker.load_and_process(app, job_id)
        db.session.expire_all()
        assert db.session.get(BatchJob, job_id).status == "queued" and staged.exists()
        assert batch_worker.claim("second-worker") == job_id
        batch_worker.load_and_process(app, job_id)
        db.session.expire_all()
        assert db.session.get(BatchJob, job_id).status == "done"
    assert attempts == 2 and not staged.exists()
    assert len(client.get("/api/me/toolkits", headers=headers["seller"]).json) == 1


def test_batch_failed_terminal_delete_is_retried_by_sweep(env, monkeypatch):
    import batch_worker

    app, client, _, headers, _ = env
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    remote, deleted = _mock_batch_object_store(monkeypatch)
    delete_remote = batch_worker.storage.delete_file
    attempts = []

    def delete_once_failed(key):
        attempts.append(key)
        if len(attempts) == 1:
            raise OSError("DELETE unavailable")
        delete_remote(key)

    monkeypatch.setattr(batch_worker.storage, "delete_file", delete_once_failed)
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("delete later")]), "p.zip")}, headers=headers["seller"])
    job_id = response.json["jobId"]
    with app.app_context():
        assert batch_worker.claim("worker") == job_id
        batch_worker.load_and_process(app, job_id)
        job = db.session.get(BatchJob, job_id)
        key = job.package_object_key
        assert job.status == "done" and key in remote and attempts == [key]
        batch_worker.cleanup_completed_inputs(app, retry_after=0)
        db.session.expire_all()
        assert db.session.get(BatchJob, job_id).package_object_key is None
        batch_worker.cleanup_completed_inputs(app, retry_after=0)
    assert attempts == [key, key] and deleted == [key] and not remote


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


def test_incomplete_draft_can_be_saved_but_not_published(env):
    _, client, _, headers, _ = env
    created = client.post("/api/toolkits", json={
        "title": "", "description": "", "price": "", "status": "draft",
        "lessons": [{"title": "", "content": "", "duration": 0}],
    }, headers=headers["seller"])
    assert created.status_code == 201, created.json
    tid = created.json["id"]
    draft = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert draft["status"] == "draft" and draft["title"] == "" and draft["description"] == ""
    assert draft["price"] == 0 and draft["lessons"][0]["title"] == ""
    rejected = client.put(f"/api/toolkits/{tid}/status", json={"status": "published"}, headers=headers["seller"])
    assert rejected.status_code == 400
    assert client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json["status"] == "draft"

    no_lessons = client.post("/api/toolkits", json={"title": "unfinished", "description": "d",
                               "status": "published"}, headers=headers["seller"])
    assert no_lessons.status_code == 400
    empty_body = client.post("/api/toolkits", json={
        "title": "unfinished", "description": "d", "status": "published",
        "lessons": [{"title": "intro", "content": "", "duration": 10}],
    }, headers=headers["seller"])
    assert empty_body.status_code == 400
    default_published = client.post("/api/toolkits", json={"title": "unfinished", "description": "d"},
                                    headers=headers["seller"])
    assert default_published.status_code == 400

    unsafe = client.post("/api/toolkits", json={
        "title": "", "description": "", "status": "draft",
        "lessons": [{"title": "", "content": "", "duration": 601}],
    }, headers=headers["seller"])
    assert unsafe.status_code == 400


def test_draft_status_matrix(env):
    app, client, _, headers, _ = env
    created = client.post("/api/toolkits", json={"title": "draft flow", "description": "d", "status": "draft",
                                                  "lessons": [{"title": "intro", "content": "body", "duration": 10}]},
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


def test_batch_published_completeness_and_draft_updates(env):
    app, client, _, headers, _ = env
    empty = {"title": "batch empty", "description": "description", "lessons": []}
    _, invalid = upload_batch(env, [empty])
    assert invalid["failed"] == 1 and "至少添加一个教程步骤" in invalid["results"][0]["error"]
    _, draft = upload_batch(env, [empty], form={"publish": "0"})
    assert draft["created"] == 1
    draft_id = draft["results"][0]["toolkitId"]
    _, skipped = upload_batch(env, [empty], form={"mode": "skip", "publish": "1"})
    assert skipped["skipped"] == 1 and skipped["results"][0]["toolkitId"] == draft_id
    _, updated = upload_batch(env, [batch_item("batch empty")], form={"mode": "update", "publish": "1"})
    assert updated["updated"] == 1
    assert client.get(f"/api/toolkits/{draft_id}", headers=headers["seller"]).json["status"] == "draft"

    _, created = upload_batch(env, [batch_item("published complete")])
    published_id = created["results"][0]["toolkitId"]
    _, rejected = upload_batch(env, [{**empty, "title": "published complete"}],
                               form={"mode": "update", "publish": "0"})
    assert rejected["failed"] == 1 and "至少添加一个教程步骤" in rejected["results"][0]["error"]
    detail = client.get(f"/api/toolkits/{published_id}", headers=headers["seller"]).json
    assert detail["status"] == "published" and len(detail["lessons"]) == 2
    with app.app_context():
        assert db.session.query(Toolkit).count() == 2


def test_non_owner_edit_is_rejected_before_file_staging_or_conversion(env, monkeypatch):
    import services.toolkit as toolkit_service

    _, client, _, headers, _ = env
    _, created = upload_batch(env, [batch_item("protected document")])
    tid = created["results"][0]["toolkitId"]

    def should_not_prepare(*_args, **_kwargs):
        pytest.fail("Unauthorized edit prepared files before checking ownership")

    with monkeypatch.context() as patch:
        patch.setattr(toolkit_service, "_prepare_item_assets", should_not_prepare)
        patch.setattr(toolkit_service, "_prepare_raw_documents", should_not_prepare)
        denied = client.put(f"/api/toolkits/{tid}", json={"lessons": [
            {"title": "Unauthorized lesson", "content": "body"}]}, headers=headers["other"])
    assert denied.status_code == 403, denied.json



def test_batch_document_original_matches_single_upload(env):
    from docx import Document

    app, client, _, headers, _ = env
    document = Document()
    document.add_paragraph("Matching converted document content")
    raw = io.BytesIO()
    document.save(raw)
    body = raw.getvalue()
    single = client.post("/api/toolkits", data={
        "title": "single document", "description": "description", "price": "10",
        "lessons": json.dumps([{"title": "chapter", "content": "", "preview": False}]),
        "lesson_file_0": (io.BytesIO(body), "chapter.docx"),
    }, headers=headers["seller"])
    assert single.status_code == 201, single.json
    single_lesson = single.json["lessons"][0]
    _, report = upload_batch(env, [{
        "title": "batch document", "description": "description", "price": 10,
        "lessons": [{"title": "chapter", "documentFile": "lessons/chapter.docx", "preview": False}],
    }], {"lessons/chapter.docx": body})
    assert report["created"] == 1 and report["failed"] == 0, report
    batch_lesson = client.get(f"/api/toolkits/{report['results'][0]['toolkitId']}",
                              headers=headers["seller"]).json["lessons"][0]
    assert batch_lesson["contentType"] == single_lesson["contentType"] == "word"
    assert "Matching converted document content" in batch_lesson["convertedHtml"]
    assert batch_lesson["convertedHtml"] == single_lesson["convertedHtml"]
    for lesson in (single_lesson, batch_lesson):
        url = lesson["rawFileUrl"]
        assert client.get(url, headers=headers["seller"]).data == body
        assert client.get(url).status_code == 403
        assert client.get(url, headers=headers["other"]).status_code == 403
    with app.app_context():
        assert db.session.query(FileReference).filter_by(usage="lesson_raw").count() == 2
    clear = {"id": single_lesson["id"], "title": "chapter", "content": "Fresh Markdown body",
             "duration": 10, "preview": False, "clearDocument": True}
    invalid = client.put(f"/api/toolkits/{single.json['id']}", json={
        "lessons": [{**clear, "content": "   "}]}, headers=headers["seller"])
    assert invalid.status_code == 400 and "非空正文" in invalid.json["message"]
    contradictory = client.put(f"/api/toolkits/{single.json['id']}", data={
        "lessons": json.dumps([clear]), "lesson_file_0": (io.BytesIO(body), "chapter.docx")},
        headers=headers["seller"])
    assert contradictory.status_code == 400 and "不可同时清除与上传" in contradictory.json["message"]
    updated = client.put(f"/api/toolkits/{single.json['id']}", json={"lessons": [clear]},
                         headers=headers["seller"])
    assert updated.status_code == 200, updated.json
    lesson = updated.json["lessons"][0]
    assert lesson["id"] == single_lesson["id"] and lesson["contentType"] == "markdown"
    assert lesson["content"] == "Fresh Markdown body" and not lesson.get("rawFileUrl")
    assert client.get(single_lesson["rawFileUrl"], headers=headers["seller"]).status_code == 404
    with app.app_context():
        assert db.session.query(FileReference).filter_by(usage="lesson_raw").count() == 1


def test_batch_update_retains_and_explicitly_clears_document_and_images(env):
    app, client, _, headers, _ = env
    cover, image = png_bytes(), png_bytes()
    _, created = upload_batch(env, [{
        "title": "originals", "description": "description", "cover": "covers/cover.png",
        "lessons": [{"title": "chapter", "documentFile": "lessons/original.md",
                     "image": "lessons/diagram.png"}],
    }], {"covers/cover.png": cover, "lessons/diagram.png": image,
        "lessons/original.md": b"# Original text"})
    assert created["created"] == 1 and created["failed"] == 0, created
    tid = created["results"][0]["toolkitId"]
    before = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    old = before["lessons"][0]
    assert client.get(old["rawFileUrl"], headers=headers["seller"]).data == b"# Original text"
    _, retained = upload_batch(env, [{
        "title": "originals", "description": "description",
        "lessons": [{"id": old["id"], "title": "chapter", "content": "Fallback text"}],
    }], form={"mode": "update"})
    assert retained["updated"] == 1 and retained["failed"] == 0, retained
    detail = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert detail["cover"] == before["cover"] and detail["lessons"][0]["image"] == old["image"]
    assert detail["lessons"][0]["rawFileUrl"] == old["rawFileUrl"]
    assert client.get(old["rawFileUrl"], headers=headers["seller"]).data == b"# Original text"
    _, cleared = upload_batch(env, [{
        "title": "originals", "description": "description",
        "lessons": [{"id": old["id"], "title": "chapter", "content": "Fresh text", "clearDocument": True}],
    }], form={"mode": "update"})
    assert cleared["updated"] == 1 and cleared["failed"] == 0, cleared
    detail = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert detail["lessons"][0]["contentType"] == "markdown"
    assert detail["lessons"][0]["content"] == "Fresh text"
    assert not detail["lessons"][0].get("rawFileUrl")
    assert client.get(old["rawFileUrl"], headers=headers["seller"]).status_code == 404
    with app.app_context():
        assert db.session.query(FileReference).filter_by(usage="lesson_raw", toolkit_id=tid).count() == 0


def test_batch_bad_document_is_isolated_and_package_error_is_actionable(env):
    app, client, _, headers, _ = env
    _, report = upload_batch(env, [{
        "title": "bad original", "description": "description",
        "lessons": [{"title": "chapter", "documentFile": "lessons/bad.pdf"}],
    }, batch_item("healthy")], {"lessons/bad.pdf": b"not a PDF"})
    assert report["failed"] == 1 and report["created"] == 1, report
    assert "第 1 步文档解析失败" in report["results"][0]["error"]
    assert client.get("/api/toolkits?q=bad+original").json["total"] == 0
    response = client.post("/api/batch/upload", data={
        "package": (package([batch_item("invalid")], {"../escape.txt": b"oops"}), "invalid.zip")},
        headers=headers["seller"])
    assert response.status_code == 202
    result = drain(app, client, headers["seller"])
    assert result["status"] == "failed" and "相对路径" in result["error"]
    assert "/root/" not in result["error"] and "/tmp/" not in result["error"]



def test_batch_omitted_lessons_preserve_existing_steps(env):
    _, client, _, headers, _ = env
    _, created = upload_batch(env, [batch_item("metadata only")], form={"publish": "0"})
    tid = created["results"][0]["toolkitId"]
    before = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    _, draft_edit = upload_batch(env, [{"title": "metadata only", "description": "updated draft"}],
                                 form={"mode": "update"})
    assert draft_edit["updated"] == 1 and draft_edit["failed"] == 0, draft_edit
    detail = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert detail["status"] == "draft" and detail["description"] == "updated draft"
    assert [lesson["id"] for lesson in detail["lessons"]] == [lesson["id"] for lesson in before["lessons"]]
    assert [lesson["content"] for lesson in detail["lessons"]] == [lesson["content"] for lesson in before["lessons"]]
    assert client.put(f"/api/toolkits/{tid}/status", json={"status": "published"},
                      headers=headers["seller"]).status_code == 200
    _, published_edit = upload_batch(env, [{"title": "metadata only", "description": "updated published"}],
                                     form={"mode": "update", "publish": "0"})
    assert published_edit["updated"] == 1 and published_edit["failed"] == 0, published_edit
    detail = client.get(f"/api/toolkits/{tid}").json
    assert detail["status"] == "published" and detail["description"] == "updated published"
    assert [lesson["id"] for lesson in detail["lessons"]] == [lesson["id"] for lesson in before["lessons"]]


def test_batch_metadata_update_preserves_document_and_resource(env):
    app, client, _, headers, _ = env
    md = b"# A disposable tutorial\n\nBody\n"
    resource = b"A disposable attachment\n"
    item = {"title": "metadata with files", "description": "original", "price": 0,
            "resource": "resources/file.txt", "lessons": [{"title": "Lesson",
            "contentFile": "lessons/body.md", "documentFile": "lessons/body.md",
            "preview": True, "duration": 5}]}
    _, created = upload_batch(env, [item], {"lessons/body.md": md, "resources/file.txt": resource},
                              form={"publish": "0"})
    tid = created["results"][0]["toolkitId"]
    before = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    _, skipped = upload_batch(env, [item], {"lessons/body.md": md, "resources/file.txt": resource},
                              form={"mode": "skip", "publish": "1"})
    assert skipped["skipped"] == 1 and not skipped["created"], skipped
    _, updated = upload_batch(env, [{"title": item["title"], "description": "updated", "price": 0}],
                              form={"mode": "update", "publish": "1"})
    assert updated["updated"] == 1 and not updated["failed"], updated
    after = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json
    assert after["status"] == "draft" and after["description"] == "updated"
    assert after["lessons"][0]["id"] == before["lessons"][0]["id"]
    assert after["resources"][0]["refId"] == before["resources"][0]["refId"]
    assert client.get(f"/api/lesson-raw/{after['lessons'][0]['id']}",
                      headers=headers["seller"]).data == md
    ref_id = after["resources"][0]["refId"]
    assert client.get(f"/api/toolkits/{tid}/resources/{ref_id}/download",
                      headers=headers["seller"]).data == resource



def test_batch_can_insert_before_explicit_existing_lesson_ids(env):
    _, client, _, headers, _ = env
    _, created = upload_batch(env, [batch_item("insert first")])
    tid = created["results"][0]["toolkitId"]
    original = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json["lessons"]
    replacement = [{"title": "new introduction", "content": "new body"},
                   {"id": original[0]["id"], "title": "preview", "content": "public body", "preview": True},
                   {"id": original[1]["id"], "title": "full", "content": "paid body", "preview": False}]
    _, updated = upload_batch(env, [{"title": "insert first", "description": "description", "lessons": replacement}],
                              form={"mode": "update"})
    assert updated["updated"] == 1 and updated["failed"] == 0, updated
    lessons = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json["lessons"]
    assert [lesson["title"] for lesson in lessons] == ["new introduction", "preview", "full"]
    assert [lesson["id"] for lesson in lessons[1:]] == [lesson["id"] for lesson in original]
    assert lessons[0]["id"] not in [lesson["id"] for lesson in original]


def test_non_markdown_original_cannot_leave_stale_preview_after_text_update(env):
    from docx import Document

    _, client, _, headers, _ = env
    document = Document()
    document.add_paragraph("Original DOCX preview")
    stream = io.BytesIO()
    document.save(stream)
    _, created = upload_batch(env, [{"title": "docx edit", "description": "description",
        "lessons": [{"title": "chapter", "documentFile": "lessons/chapter.docx"}]}],
        {"lessons/chapter.docx": stream.getvalue()})
    tid = created["results"][0]["toolkitId"]
    old = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json["lessons"][0]
    text_edit = {"title": "docx edit", "description": "description",
                 "lessons": [{"id": old["id"], "title": "chapter", "content": "New text"}]}
    _, rejected = upload_batch(env, [text_edit], form={"mode": "update"})
    assert rejected["failed"] == 1 and "不可单独修改正文" in rejected["results"][0]["error"]
    single = client.put(f"/api/toolkits/{tid}", json={"lessons": text_edit["lessons"]}, headers=headers["seller"])
    assert single.status_code == 400 and "不可单独修改正文" in single.json["message"]
    after = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json["lessons"][0]
    assert after["content"] == old["content"] and after["convertedHtml"] == old["convertedHtml"]
    assert after["rawFileUrl"] == old["rawFileUrl"]
    _, cleared = upload_batch(env, [{**text_edit, "lessons": [{**text_edit["lessons"][0],
                                  "clearDocument": True}]}], form={"mode": "update"})
    assert cleared["updated"] == 1 and cleared["failed"] == 0, cleared
    after = client.get(f"/api/toolkits/{tid}", headers=headers["seller"]).json["lessons"][0]
    assert after["contentType"] == "markdown" and after["content"] == "New text"
    assert not after.get("rawFileUrl") and after["convertedHtml"] is None


def test_markdown_original_and_rendered_preview_are_bounded(env, monkeypatch):
    _, client, _, headers, _ = env
    large = b"x" * (1024 * 1024 + 1)
    _, report = upload_batch(env, [{"title": "oversized original", "description": "description",
        "lessons": [{"title": "chapter", "documentFile": "lessons/large.md"}]}],
        {"lessons/large.md": large})
    assert report["failed"] == 1 and "正文超过 1MB" in report["results"][0]["error"]
    single = client.post("/api/toolkits", data={"title": "oversized single", "description": "description",
        "lessons": json.dumps([{"title": "chapter", "content": ""}]),
        "lesson_file_0": (io.BytesIO(large), "large.md")}, headers=headers["seller"])
    assert single.status_code == 400 and "原件正文超过 1MB" in single.json["message"]
    monkeypatch.setattr("services.toolkit.convert_document", lambda *_: "x" * (4 * 1024 * 1024 + 1))
    _, oversized_html = upload_batch(env, [{"title": "oversized rendered", "description": "description",
        "lessons": [{"title": "chapter", "documentFile": "lessons/small.md"}]}],
        {"lessons/small.md": b"# text"})
    assert oversized_html["failed"] == 1 and "预览超过 4MB" in oversized_html["results"][0]["error"]
    with env[0].app_context():
        assert db.session.query(Toolkit).count() == 0
