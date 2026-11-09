"""In-process asynchronous batch import worker.

No external broker is provisioned on the target box, so imports run on a daemon
thread per Gunicorn worker process. Correctness relies on a durable BatchJob row
and an atomic claim (SELECT ... FOR UPDATE SKIP LOCKED on MySQL) so concurrent
workers never process the same job. Each item uses its own transaction; once
item processing has started, uncertain failures cannot be replayed automatically
without duplicating create-mode items. Only pre-item failures are requeued.
"""
import hashlib
import json
import os
import threading
import time
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from extensions import db
from models import BatchJob, Toolkit, User, utcnow
from security import APIError, Identity, assert_identity
from file_service import init_storage
import storage
from services.toolkit import persist_item
import money_service as money


_ITEMS_STARTED = "items:"
# Stable DB marker: terminal inputs awaiting reconciliation must never be swept.
_RETAINED_CLAIM = "batch:retained"
# This prefix encodes a durable retry count in BatchJob.error; keep it stable.
_RETRY_NOTE = "批量包暂不可用，等待重试 "
_RETRY_LIMIT = 3
_RETRY_DELAY_SECONDS = 30
_RETAINED_ERROR = "批量导入中断，输入包已保留，需人工核对后重试"


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
    from flask import current_app

    remote = current_app.config["FILE_STORAGE_BACKEND"] == "s3"
    object_key, digest = None, None
    try:
        if remote:
            checksum = hashlib.sha256()
            with open(path, "rb") as source:
                while chunk := source.read(128 * 1024):
                    checksum.update(chunk)
            digest = checksum.hexdigest()
            object_key = f"files/{uuid.uuid4().hex}"
            try:
                storage.put_file(object_key, path, size, digest)
            except Exception:
                # PUT can store the object and then fail during HEAD/readback.
                # No DB transaction has started yet, so this key is safe to reclaim.
                try:
                    storage.delete_file(object_key)
                except Exception:
                    current_app.logger.warning("Failed to clean batch upload %s at %s", job_id, object_key)
                raise
        commit_pending = False
        try:
            with Session(db.engine) as session, session.begin():
                user = money.lock_users(session, [identity.uid])[identity.uid]
                assert_identity(session, user, identity)
                session.add(BatchJob(id=job_id, owner_id=user.id, status="queued", mode=mode,
                                     publish=publish, package_path=None if remote else path,
                                     package_object_key=object_key, package_sha256=digest, package_size=size))
                commit_pending = True
        except Exception:
            if commit_pending:
                # COMMIT may have succeeded even if its acknowledgement was lost.
                # Keep the only input for a possibly queued job in either backend.
                if remote:
                    current_app.logger.warning("Batch enqueue DB outcome uncertain for job %s; retained object %s for reconciliation",
                                               job_id, object_key)
                else:
                    current_app.logger.warning("Batch enqueue DB outcome uncertain for job %s; retained staged ZIP for reconciliation",
                                               job_id)
            elif remote:
                # No commit was attempted, so the object cannot be referenced.
                try:
                    storage.delete_file(object_key)
                except Exception:
                    current_app.logger.warning("Failed to clean unqueued batch package %s at %s", job_id, object_key)
            else:
                _safe_unlink(path)
            raise
    finally:
        # Remote staging is disposable after verified PUT or a failed upload;
        # local jobs must retain their input until a worker consumes it (or an
        # uncertain transaction outcome is reconciled).
        if remote:
            _safe_unlink(path)


def claim(worker_id):
    """Atomically move one queued job to running under this worker."""
    dialect = db.engine.dialect.name
    retry_cutoff = utcnow() - timedelta(seconds=_RETRY_DELAY_SECONDS)
    stmt = select(BatchJob).where(BatchJob.status == "queued", or_(
        BatchJob.error.is_(None), ~BatchJob.error.startswith(_RETRY_NOTE),
        BatchJob.updated_at <= retry_cutoff)).order_by(BatchJob.created_at, BatchJob.id).limit(1)
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
    """Retry only jobs that cannot yet have imported an item.

    A worker can die after an item commits but before its progress is recorded.
    The durable claimed_by marker is written before the first item starts; such
    jobs require reconciliation, never an automatic replay in create mode.
    """
    cutoff = utcnow() - timedelta(seconds=seconds)
    with Session(db.engine) as session, session.begin():
        jobs = session.execute(select(BatchJob.id, BatchJob.claimed_by, BatchJob.processed,
                                      BatchJob.package_object_key, BatchJob.error).where(
            BatchJob.status == "running", BatchJob.claimed_at < cutoff)).all()
        for job_id, claimed_by, processed, key, error in jobs:
            attempts = _retry_count(error) + 1
            uncertain = ((claimed_by or "").startswith(_ITEMS_STARTED) or processed > 0
                         or attempts >= _RETRY_LIMIT)
            values = {"status": "failed" if uncertain else "queued",
                      "claimed_by": _RETAINED_CLAIM if uncertain else None, "claimed_at": None,
                      "updated_at": utcnow(),
                      "error": _RETAINED_ERROR if uncertain else f"{_RETRY_NOTE}{attempts}/{_RETRY_LIMIT}"}
            # A live worker may have updated its marker/progress since SELECT.
            guard = BatchJob.claimed_by == claimed_by if claimed_by is not None else BatchJob.claimed_by.is_(None)
            changed = session.execute(update(BatchJob).where(
                BatchJob.id == job_id, BatchJob.status == "running", BatchJob.claimed_at < cutoff,
                BatchJob.processed == processed, guard).values(**values)).rowcount
            if changed and uncertain:
                from flask import current_app
                current_app.logger.warning("Batch job %s input %s retained for reconciliation after lost worker",
                                           job_id, key or "local")


def _renew_claim(app, job_id, worker_id, stop):
    """Keep a still-owned job live while a document or whole item is converting."""
    interval = max(0.25, min(60.0, app.config.get("BATCH_CLAIM_TIMEOUT_SECONDS", 600) / 4))
    claims = (worker_id, f"{_ITEMS_STARTED}{worker_id}")
    with app.app_context():
        while not stop.wait(interval):
            try:
                with Session(db.engine) as session, session.begin():
                    renewed = session.execute(update(BatchJob).where(
                        BatchJob.id == job_id, BatchJob.status == "running",
                        BatchJob.claimed_by.in_(claims)).values(claimed_at=utcnow())).rowcount
                if not renewed:
                    return
            except SQLAlchemyError as error:
                app.logger.warning("Batch job %s lease renewal failed (%s)", job_id, type(error).__name__)


def load_and_process(app, job_id, worker_id=None):
    """Process a claimed job, downloading remote input into bounded disk storage."""
    with Session(db.engine) as session:
        job = session.get(BatchJob, job_id)
        if not job:
            return
        owner_id, mode, publish, stored_path = job.owner_id, job.mode, job.publish, job.package_path
        key, digest, size = job.package_object_key, job.package_sha256, job.package_size
        claim_owner = worker_id or (job.claimed_by or "").removeprefix(_ITEMS_STARTED)

    lease_stop = threading.Event()
    lease_thread = None
    if job.status == "running" and claim_owner:
        lease_thread = threading.Thread(target=_renew_claim, args=(app, job_id, claim_owner, lease_stop),
                                        name=f"batch-lease-{job_id[:8]}", daemon=True)
        lease_thread.start()
    try:
        if key:
            with TemporaryDirectory(prefix="zhihe-batch-input-") as staging:
                path = Path(staging) / f"{job_id}.zip"
                with path.open("w+b") as output:
                    storage.read_to_file(key, output, size, digest, app.config["MAX_CONTENT_LENGTH"])
                _process_staged_package(app, job_id, owner_id, mode, publish, str(path), claim_owner)
        else:
            _process_staged_package(app, job_id, owner_id, mode, publish, stored_path, claim_owner)
    except Exception:
        app.logger.exception("Batch package %s cannot be processed (input %s)", job_id, key or "local")
        _record_processing_failure(job_id, key, claim_owner)
    finally:
        if lease_thread:
            lease_stop.set()
            lease_thread.join(timeout=5)
        if key:
            # Idempotent DELETE + conditional key clearing; a failure remains
            # discoverable by the worker sweep on its next eligible pass.
            cleanup_completed_inputs(app, job_id=job_id)


def _process_staged_package(app, job_id, owner_id, mode, publish, stored_path, worker_id=None):
    from batch_kit import load_batch_package, ManifestError
    from file_service import UploadContext

    if not stored_path or not os.path.exists(stored_path):
        _finish(job_id, status="failed", worker_id=worker_id, error="批量包文件已丢失，请重新上传")
        return
    with Session(db.engine) as session:
        owner = session.get(User, owner_id)
    if owner is None:
        if _finish(job_id, status="failed", worker_id=worker_id, error="作者账号不存在"):
            _safe_unlink(stored_path)
        return
    identity = _identity_from_user(owner, f"batch:{job_id}")
    completed = False
    try:
        # The staging dir must stay alive for the WHOLE import: item
        # descriptors reference staged member files until each _persist_item
        # has copied what it needs into the owned storage.
        with TemporaryDirectory(prefix="zhihe-batch-") as staging:
            try:
                with zipfile.ZipFile(stored_path) as zf:
                    items, errors = load_batch_package(zf, staging)
            except ManifestError:
                raise
            except (zipfile.BadZipFile, ValueError, RuntimeError):
                raise ManifestError("批量 ZIP 无效或损坏")
            _set_total(job_id, len(items) + len(errors), worker_id)
            for error in errors:
                _tick(job_id, worker_id, failed=1, record=error)
            if items:
                _mark_items_started(job_id, worker_id)
            uploads = UploadContext(identity)
            for item in items:
                try:
                    outcome = _import_item(item, identity, uploads, mode, publish)
                    status = outcome["status"]
                    if status == "skipped":
                        uploads.resolve_failure()
                        message = "已有同名工具包，已跳过"
                        _tick(job_id, worker_id, skipped=1, record={"index": item["index"], "title": item["title"],
                              "status": status, "toolkitId": outcome["toolkitId"],
                              "message": message, "error": message})
                    elif status == "updated":
                        _tick(job_id, worker_id, updated=1, record={"index": item["index"], "title": item["title"],
                              "status": status, "toolkitId": outcome["toolkitId"],
                              "lessonCount": len(item["lessons"] or [])})
                    else:
                        _tick(job_id, worker_id, created=1, record={"index": item["index"], "title": item["title"],
                              "status": status, "toolkitId": outcome["toolkitId"],
                              "lessonCount": len(item["lessons"])})
                except SQLAlchemyError:
                    # An item commit can have succeeded despite a lost DB reply.
                    # Neither replay nor cleanup is safe until reconciled.
                    raise
                except (APIError, ValueError) as error:
                    uploads.resolve_failure()
                    message = error.message if isinstance(error, APIError) else str(error)
                    _tick(job_id, worker_id, failed=1, record={"index": item["index"], "title": item["title"],
                          "status": "failed", "message": message, "error": message})
                except Exception as error:  # noqa: BLE001 - per-item isolation is the contract
                    uploads.resolve_failure()
                    app.logger.warning("Batch item %s failed unexpectedly (%s)", item.get("index"), type(error).__name__)
                    message = "条目处理失败，请重试文件或数据服务"
                    _tick(job_id, worker_id, failed=1, record={"index": item["index"], "title": item.get("title", ""),
                          "status": "failed", "message": message, "error": message})
        completed = _finish(job_id, status="done", worker_id=worker_id)
    except ManifestError as error:
        detail = str(error)
        if detail.startswith("无法解析批量 ZIP："):
            detail = "ZIP 包读取失败，请检查文件完整性及格式"
        completed = _finish(job_id, status="failed", worker_id=worker_id,
                            error=detail[:240] or "批量包不符合规范")
    finally:
        # A retry or reconciliation still needs the original input on local
        # storage. Remote jobs use a separate temporary download directory.
        if completed:
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


def _mark_items_started(job_id, worker_id=None):
    """Persist a no-replay marker before an item can cause external effects."""
    with Session(db.engine) as session, session.begin():
        job = session.execute(select(BatchJob).where(BatchJob.id == job_id)
                              .with_for_update()).scalar_one_or_none()
        if (job is None or job.status != "running" or not job.claimed_by
                or worker_id is not None and job.claimed_by != worker_id):
            raise RuntimeError("Batch job lost its worker claim")
        if not job.claimed_by.startswith(_ITEMS_STARTED):
            job.claimed_by = f"{_ITEMS_STARTED}{job.claimed_by}"


def _retry_count(error):
    if not error or not error.startswith(_RETRY_NOTE):
        return 0
    value = error[len(_RETRY_NOTE):].partition("/")[0]
    return int(value) if value.isdecimal() else 0


def _record_processing_failure(job_id, key, worker_id=None):
    """Retry only failures before any item could have committed.

    Post-item failures and exhausted retries are quarantined with their input
    intact for reconciliation; replaying create mode would duplicate toolkits.
    """
    from flask import current_app

    with Session(db.engine) as session, session.begin():
        job = session.execute(select(BatchJob).where(BatchJob.id == job_id)
                              .with_for_update()).scalar_one_or_none()
        if job is None or job.status != "running":
            return
        if worker_id is not None and job.claimed_by not in (worker_id, f"{_ITEMS_STARTED}{worker_id}"):
            # Another worker owns the lease; do not alter its job state.
            return
        if (job.claimed_by or "").startswith(_ITEMS_STARTED) or job.processed > 0:
            job.status = "failed"
            job.error = _RETAINED_ERROR
            current_app.logger.warning("Batch job %s input %s retained for reconciliation after item processing",
                                       job_id, key or "local")
        else:
            attempts = _retry_count(job.error) + 1
            if attempts >= _RETRY_LIMIT:
                job.status = "failed"
                job.error = _RETAINED_ERROR
                current_app.logger.warning("Batch job %s input %s retained after %s failed pre-item attempts",
                                           job_id, key or "local", attempts)
            else:
                job.status = "queued"
                job.error = f"{_RETRY_NOTE}{attempts}/{_RETRY_LIMIT}"
                current_app.logger.warning("Batch job %s input %s queued for safe retry (%s/%s)",
                                           job_id, key or "local", attempts, _RETRY_LIMIT)
        job.claimed_by = _RETAINED_CLAIM if job.status == "failed" else None
        job.claimed_at = None


def cleanup_completed_inputs(app, job_id=None, limit=10, retry_after=60):
    """Sweep terminal S3 inputs; DELETE is idempotent if DB bookkeeping fails.

    A failed DELETE leaves the key on the row and postpones the next attempt.
    Inputs deliberately retained for manual reconciliation are never swept.
    """
    cutoff = utcnow() - timedelta(seconds=retry_after)
    with Session(db.engine) as session:
        stmt = select(BatchJob.id, BatchJob.package_object_key).where(
            BatchJob.status.in_(("done", "failed")), BatchJob.package_object_key.is_not(None),
            or_(BatchJob.claimed_by.is_(None), BatchJob.claimed_by != _RETAINED_CLAIM))
        if job_id is not None:
            stmt = stmt.where(BatchJob.id == job_id)
        else:
            stmt = stmt.where(or_(BatchJob.updated_at.is_(None), BatchJob.updated_at <= cutoff))
            stmt = stmt.order_by(BatchJob.updated_at, BatchJob.id).limit(limit)
        candidates = session.execute(stmt).all()
    for completed_id, key in candidates:
        try:
            storage.delete_file(key)
        except Exception:
            app.logger.warning("Batch input cleanup deferred for job %s key %s", completed_id, key)
            with Session(db.engine) as session, session.begin():
                session.execute(update(BatchJob).where(
                    BatchJob.id == completed_id, BatchJob.package_object_key == key,
                    BatchJob.status.in_(("done", "failed"))).values(updated_at=utcnow()))
            continue
        with Session(db.engine) as session, session.begin():
            session.execute(update(BatchJob).where(
                BatchJob.id == completed_id, BatchJob.package_object_key == key,
                BatchJob.status.in_(("done", "failed")),
                or_(BatchJob.claimed_by.is_(None), BatchJob.claimed_by != _RETAINED_CLAIM)
            ).values(package_object_key=None, package_sha256=None))


def _set_total(job_id, total, worker_id):
    with Session(db.engine) as session, session.begin():
        changed = session.execute(update(BatchJob).where(
            BatchJob.id == job_id, BatchJob.status == "running",
            BatchJob.claimed_by == worker_id).values(total=total)).rowcount
        if not changed:
            raise RuntimeError("Batch job lost its worker claim")


def _tick(job_id, worker_id, created=0, updated=0, skipped=0, failed=0, record=None):
    with Session(db.engine) as session, session.begin():
        job = session.execute(select(BatchJob).where(BatchJob.id == job_id)
                              .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if job is None or job.status != "running" or job.claimed_by not in (
                worker_id, f"{_ITEMS_STARTED}{worker_id}"):
            raise RuntimeError("Batch job no longer has an active claim")
        job.claimed_at = utcnow()  # Keep a live per-item worker from expiring its lease.
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


def _finish(job_id, status, worker_id, error=None):
    with Session(db.engine) as session, session.begin():
        changed = session.execute(update(BatchJob).where(
            BatchJob.id == job_id, BatchJob.status == "running",
            BatchJob.claimed_by.in_((worker_id, f"{_ITEMS_STARTED}{worker_id}"))).values(
                status=status, error=error, claimed_by=None, claimed_at=None)).rowcount
    return bool(changed)


def _safe_unlink(path):
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


def _worker_loop(app, worker_id):
    """One worker loop. Unexpected errors are logged and the loop continues.
    Stale pre-item jobs requeue; jobs that may have imported items are retained
    for reconciliation rather than replayed."""
    next_cleanup = 0.0
    while True:
        if getattr(app, "_zhihe_batch_stop", None) and app._zhihe_batch_stop.is_set():
            return
        job_id = None
        try:
            with app.app_context():
                if time.monotonic() >= next_cleanup:
                    cleanup_completed_inputs(app)
                    next_cleanup = time.monotonic() + 30
                requeue_stale(app.config.get("BATCH_CLAIM_TIMEOUT_SECONDS", 600))
                job_id = claim(worker_id)
                if job_id:
                    load_and_process(app, job_id, worker_id)
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
