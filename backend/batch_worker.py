"""In-process asynchronous batch import worker.

No external broker is provisioned on the target box, so imports run on a daemon
thread per Gunicorn worker process. Correctness relies on a durable BatchJob row
and an atomic claim (SELECT ... FOR UPDATE SKIP LOCKED on MySQL) so concurrent
workers never process the same job. Each item reuses _persist_item's independent
transaction, so a failure or worker restart leaves committed items intact; stale
running jobs are requeued after a lease timeout.
"""
import json
import os
import threading
import time
import uuid
import zipfile
from datetime import timedelta
from tempfile import TemporaryDirectory

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from extensions import db
from models import BatchJob, Toolkit, User, utcnow
from security import APIError, Identity, assert_identity
from file_service import init_storage
from services.toolkit import persist_item
import money_service as money


def _identity_from_user(user, jti):
    """Background identity snapshot: the user's current token_version with a
    far-future expiry and a synthetic jti that can never appear in revocations.
    assert_identity still relocks the User and compares token_version inside
    every item transaction, so a password change or logout mid-import fails the
    remaining items instead of proceeding with stale authority."""
    return Identity(user.id, user.token_version, jti, utcnow() + timedelta(days=3650))


def stage_package(stream, maximum):
    """Stream an upload to the durable jobs dir with a hard byte ceiling. Never
    materialize the whole package in memory. Returns (job_id, path, size)."""
    init_storage()
    job_id = uuid.uuid4().hex
    root = os.path.join(current_upload_root(), ".batches")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, f"{job_id}.zip")
    total = 0
    try:
        with open(path, "xb") as output:
            while True:
                chunk = stream.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise APIError("批量包超过大小限制", 413)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return job_id, path, total


def current_upload_root():
    from flask import current_app
    return current_app.config["UPLOAD_FOLDER"]


def enqueue(identity, mode, publish, job_id, path, size):
    with Session(db.engine) as session, session.begin():
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        session.add(BatchJob(id=job_id, owner_id=user.id, status="queued", mode=mode,
                             publish=publish, package_path=path, package_size=size))


def claim(worker_id):
    """Atomically move one queued job to running under this worker."""
    dialect = db.engine.dialect.name
    stmt = select(BatchJob).where(BatchJob.status == "queued").order_by(
        BatchJob.created_at, BatchJob.id).limit(1)
    # MySQL 8 supports SKIP LOCKED; other dialects use the plain row lock.
    stmt = stmt.with_for_update(skip_locked=True) if dialect == "mysql" else stmt.with_for_update()
    with Session(db.engine) as session, session.begin():
        job = session.execute(stmt).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.claimed_by = worker_id
        job.claimed_at = utcnow()
        return job.id


def requeue_stale(seconds):
    """Return lease-expired running jobs to the queue (a worker died mid-job)."""
    cutoff = utcnow() - timedelta(seconds=seconds)
    with Session(db.engine) as session, session.begin():
        ids = session.execute(select(BatchJob.id).where(BatchJob.status == "running",
                      BatchJob.claimed_at < cutoff)).scalars().all()
        for job_id in ids:
            values = {"status": "queued", "claimed_by": None, "claimed_at": None}
            # Conditional guard: only jobs still marked stale-running are touched.
            session.execute(update(BatchJob).where(BatchJob.id == job_id,
                        BatchJob.status == "running", BatchJob.claimed_at < cutoff).values(**values))


def load_and_process(app, job_id):
    """Process one claimed job to a terminal state. Inside an app context."""
    from batch_kit import load_batch_package, ManifestError
    from file_service import UploadContext
    with Session(db.engine) as session:
        job = session.get(BatchJob, job_id)
        if not job:
            return
        owner_id, mode, publish, stored_path = job.owner_id, job.mode, job.publish, job.package_path

    if not stored_path or not os.path.exists(stored_path):
        _finish(job_id, status="failed", error="批量包文件已丢失，请重新上传")
        return
    with Session(db.engine) as session:
        owner = session.get(User, owner_id)
    if owner is None:
        _finish(job_id, status="failed", error="作者账号不存在")
        _safe_unlink(stored_path)
        return
    identity = _identity_from_user(owner, f"batch:{job_id}")
    try:
        # The staging dir must stay alive for the WHOLE import: item
        # descriptors reference staged member files until each _persist_item
        # has copied what it needs into the owned storage.
        with TemporaryDirectory(prefix="zhihe-batch-") as staging:
            try:
                with zipfile.ZipFile(stored_path) as zf:
                    items, errors = load_batch_package(zf, staging)
            except (zipfile.BadZipFile, ValueError, RuntimeError):
                raise ManifestError("批量 ZIP 无效或损坏")
            _set_total(job_id, len(items) + len(errors))
            for error in errors:
                _tick(job_id, failed=1, record=error)
            uploads = UploadContext(identity)
            for item in items:
                try:
                    outcome = _import_item(item, identity, uploads, mode, publish)
                    status = outcome["status"]
                    if status == "skipped":
                        uploads.resolve_failure()
                        message = "已有同名工具包，已跳过"
                        _tick(job_id, skipped=1, record={"index": item["index"], "title": item["title"],
                              "status": status, "toolkitId": outcome["toolkitId"],
                              "message": message, "error": message})
                    elif status == "updated":
                        _tick(job_id, updated=1, record={"index": item["index"], "title": item["title"],
                              "status": status, "toolkitId": outcome["toolkitId"],
                              "lessonCount": len(item["lessons"] or [])})
                    else:
                        _tick(job_id, created=1, record={"index": item["index"], "title": item["title"],
                              "status": status, "toolkitId": outcome["toolkitId"],
                              "lessonCount": len(item["lessons"])})
                except (APIError, ValueError) as error:
                    uploads.resolve_failure()
                    message = error.message if isinstance(error, APIError) else str(error)
                    _tick(job_id, failed=1, record={"index": item["index"], "title": item["title"],
                          "status": "failed", "message": message, "error": message})
                except Exception as error:  # noqa: BLE001 - per-item isolation is the contract
                    uploads.resolve_failure()
                    app.logger.warning("Batch item %s failed unexpectedly (%s)", item.get("index"), type(error).__name__)
                    message = "条目处理失败，请重试文件或数据服务"
                    _tick(job_id, failed=1, record={"index": item["index"], "title": item.get("title", ""),
                          "status": "failed", "message": message, "error": message})
        _finish(job_id, status="done")
    except ManifestError:
        _finish(job_id, status="failed", error="ZIP 包无效、损坏或清单不符合规范")
    finally:
        _safe_unlink(stored_path)


def _import_item(item, identity, uploads, mode, publish):
    """Batch create/update semantics. skip reuses _persist_item's duplicate
    check under the owner lock; update resolves this author's same-title
    toolkit and imports over it; create never deduplicates."""
    if mode == "skip":
        result = persist_item(item, identity, uploads, skip_existing=True, publish=publish)
        if result.get("status") == "skipped":
            return {"status": "skipped", "toolkitId": result["toolkitId"]}
        return {"status": "created", "toolkitId": result["id"]}
    if mode == "update":
        with Session(db.engine) as session:
            existing_id = session.execute(select(Toolkit.id).where(
                Toolkit.author_id == identity.uid, Toolkit.title == item["title"])
                .order_by(Toolkit.id).limit(1)).scalar_one_or_none()
        if existing_id is not None:
            persist_item(item, identity, uploads, toolkit_id=existing_id, publish=publish)
            return {"status": "updated", "toolkitId": existing_id}
    result = persist_item(item, identity, uploads, publish=publish)
    return {"status": "created", "toolkitId": result["id"]}


def _set_total(job_id, total):
    with Session(db.engine) as session, session.begin():
        session.execute(update(BatchJob).where(BatchJob.id == job_id).values(total=total))


def _tick(job_id, created=0, updated=0, skipped=0, failed=0, record=None):
    with Session(db.engine) as session, session.begin():
        job = session.execute(select(BatchJob).where(BatchJob.id == job_id)
                              .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if job is None:
            return
        job.created += created
        job.updated += updated
        job.skipped += skipped
        job.failed += failed
        job.processed += 1
        if record is not None:
            try:
                results = json.loads(job.results_json or "[]")
            except (TypeError, ValueError):
                results = []
            results.append(record)
            job.results_json = json.dumps(results, ensure_ascii=False)


def _finish(job_id, status, error=None):
    with Session(db.engine) as session, session.begin():
        session.execute(update(BatchJob).where(BatchJob.id == job_id).values(
            status=status, error=error, claimed_by=None, claimed_at=None))


def _safe_unlink(path):
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


def _worker_loop(app, worker_id):
    """One worker loop. Never dies: any unexpected error is logged and the loop
    continues with the next poll. A job whose processing thread itself dies is
    recovered by requeue_stale on the next tick."""
    while True:
        if getattr(app, "_zhihe_batch_stop", None) and app._zhihe_batch_stop.is_set():
            return
        job_id = None
        try:
            with app.app_context():
                requeue_stale(app.config.get("BATCH_CLAIM_TIMEOUT_SECONDS", 600))
                job_id = claim(worker_id)
                if job_id:
                    load_and_process(app, job_id)
                    continue
        except Exception as exc:  # noqa: BLE001
            app.logger.error("Batch worker iteration failed (%s)", type(exc).__name__)
        time.sleep(2.0)


def start_workers(app, count=1):
    """Launch background batch workers once per process. Idempotent."""
    if getattr(app, "_zhihe_batch_started", False):
        return
    app._zhihe_batch_started = True
    app._zhihe_batch_stop = threading.Event()
    for index in range(count):
        worker_id = f"{os.getpid()}-{index}-{uuid.uuid4().hex[:8]}"
        threading.Thread(target=_worker_loop, args=(app, worker_id), daemon=True,
                         name=f"zhihe-batch-{index}").start()
