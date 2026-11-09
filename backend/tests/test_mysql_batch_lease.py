"""MySQL regression for renewing a batch claim during a long-running item."""
from datetime import timedelta
import threading
import time
import uuid

import pytest

pytestmark = pytest.mark.mysql


def test_processing_renews_started_item_claim_and_prevents_stale_requeue(mysql_app, mysql_data, monkeypatch):
    import batch_worker
    from extensions import db
    from models import BatchJob, utcnow
    from sqlalchemy.orm import Session

    job_id = uuid.uuid4().hex
    owner_id = mysql_data["users"]["author"]["id"]
    stale = utcnow() - timedelta(minutes=2)
    with mysql_app.app_context():
        with Session(db.engine) as session, session.begin():
            session.add(BatchJob(id=job_id, owner_id=owner_id, status="queued", mode="create",
                                 publish=False, package_path="/tmp/unused-batch-lease-input"))
        assert batch_worker.claim("lease-worker") == job_id
        batch_worker._mark_items_started(job_id, "lease-worker")
        with Session(db.engine) as session, session.begin():
            session.get(BatchJob, job_id).claimed_at = stale
        with Session(db.engine) as session:
            persisted_stale = session.get(BatchJob, job_id).claimed_at
            assert utcnow() - persisted_stale > timedelta(seconds=60)

    mysql_app.config["BATCH_CLAIM_TIMEOUT_SECONDS"] = 1
    entered, release = threading.Event(), threading.Event()
    errors = []

    def blocked_import(*_args):
        entered.set()
        assert release.wait(25), "Test worker did not receive a release signal"
        batch_worker._finish(job_id, "done", "lease-worker")

    def run_worker():
        try:
            with mysql_app.app_context():
                batch_worker.load_and_process(mysql_app, job_id, "lease-worker")
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(batch_worker, "_process_staged_package", blocked_import)
    worker = threading.Thread(target=run_worker, name="qa-batch-lease")
    worker.start()
    try:
        assert entered.wait(5), "Test worker did not enter the blocked item"
        deadline = time.monotonic() + 12
        renewed = None
        with mysql_app.app_context():
            while time.monotonic() < deadline:
                with Session(db.engine) as session:
                    job = session.get(BatchJob, job_id)
                    if job.claimed_at > persisted_stale:
                        renewed = job.claimed_at
                        break
                time.sleep(0.05)
            assert renewed is not None, "Blocked import never renewed its job claim"
            assert utcnow() - renewed < timedelta(seconds=30)
            batch_worker.requeue_stale(30)
            with Session(db.engine) as session:
                job = session.get(BatchJob, job_id)
                assert job.status == "running" and job.claimed_by == "items:lease-worker"
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive() and not errors
    with mysql_app.app_context():
        with Session(db.engine) as session:
            assert session.get(BatchJob, job_id).status == "done"
