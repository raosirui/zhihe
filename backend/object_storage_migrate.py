"""Conservative, resumable local-to-private-S3 registry migration.

Run from backend/: python object_storage_migrate.py [--database-name DB]
Apply only after reviewing its revision and taking a backup, in maintenance:
  python object_storage_migrate.py --apply --database-name DB \
    --expected-revision REV --backup-confirmed --maintenance-confirmed

This is independent of the application's historical schema migration. It never
removes local files or inventories/deletes unregistered bytes or S3 objects.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from sqlalchemy import MetaData, Table, inspect, select, text, update
from werkzeug.utils import secure_filename

from extensions import db
from file_service import KINDS, safe_stored_name
import storage


COLUMNS = {
    "file_asset": {"storage_backend": "VARCHAR(16)", "object_key": "VARCHAR(64)"},
    "batch_job": {"package_object_key": "VARCHAR(160)", "package_sha256": "VARCHAR(64)"},
}
REQUIRED = {
    "file_asset": {"id", "stored_name", "owner_id", "purpose", "size", "sha256", "state", "is_public"},
    "file_reference": {"id", "asset_id", "toolkit_id", "lesson_id", "usage", "binding_key", "name", "sort_order"},
    "batch_job": {"id", "package_path", "package_size"},
    "lesson": {"id", "toolkit_id", "raw_file_path"},
    "toolkit": {"id", "author_id"},
    "user": {"id"},
}
_CHUNK = 128 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_RAW = re.compile(r"lesson_([0-9]+)_[A-Za-z0-9._-]{1,195}\Z")


class MigrationSafetyError(RuntimeError):
    """A precondition failed; no migration data must be written."""


def _identity(engine, *, allow_test_sqlite=False):
    dialect = engine.dialect.name
    if dialect == "mysql":
        with engine.connect() as connection:
            actual = connection.execute(text("SELECT DATABASE()")).scalar_one()
        if not actual or actual != engine.url.database:
            raise MigrationSafetyError("Connected MySQL database does not match configured database")
        return actual
    if dialect == "sqlite" and allow_test_sqlite:
        from flask import current_app
        if not current_app.config.get("TESTING") or current_app.config.get("APP_ENV") != "test":
            raise MigrationSafetyError("SQLite migration is restricted to injected test applications")
        return "sqlite-test"
    raise MigrationSafetyError("A verified MySQL database is required (SQLite only in isolated tests)")


def _schema(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    problems, missing, signature = [], [], {}
    for table, required in REQUIRED.items():
        if table not in tables:
            problems.append(f"missing_table:{table}")
            continue
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        signature[table] = [(name, str(column["type"]), bool(column["nullable"]))
                            for name, column in sorted(columns.items())]
        problems.extend(f"missing_required_column:{table}.{name}" for name in sorted(required - columns.keys()))
        for name, sqltype in COLUMNS.get(table, {}).items():
            if name not in columns:
                missing.append((table, name, sqltype))
            elif (not columns[name]["nullable"] or
                  getattr(columns[name]["type"], "length", None) != int(sqltype.split("(")[1][:-1])):
                problems.append(f"incompatible_column:{table}.{name}")
    return missing, signature, problems


def _root(app):
    raw = app.config["UPLOAD_FOLDER"]
    if (not isinstance(raw, str) or not raw.startswith("/") or raw == "/" or
            ".." in raw.split("/") or "//" in raw):
        raise MigrationSafetyError("UPLOAD_FOLDER must be a normalized absolute non-root path")
    return Path(raw)


def _no_symlink_components(path):
    cursor = Path("/")
    for part in path.parts[1:]:
        cursor = cursor / part
        info = cursor.lstat()  # Do not resolve or follow any link, including a parent.
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("symlink")
        if cursor != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError("non-directory parent")


@contextmanager
def _checked_file(root, kind, name):
    """Anchor to no-follow directory FDs; the inode stays open through upload."""
    if kind not in KINDS or not isinstance(name, str) or name in {"", ".", ".."} or "/" in name or "\\" in name:
        raise ValueError("invalid file component")
    _no_symlink_components(root)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    kind_fd = fd = -1
    try:
        kind_fd = os.open(kind, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=kind_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("non-regular or hardlinked file")
        yield fd
    finally:
        if fd >= 0:
            os.close(fd)
        if kind_fd >= 0:
            os.close(kind_fd)
        os.close(root_fd)


def _fingerprint(fd):
    info = os.fstat(fd)
    digest = hashlib.sha256()
    size = 0
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, _CHUNK)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
    end = os.fstat(fd)
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if identity != (end.st_dev, end.st_ino, end.st_size, end.st_mtime_ns, end.st_ctime_ns):
        raise ValueError("file changed while reading")
    return size, digest.hexdigest(), identity


def _raw_name(lesson_id, path, root):
    if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/"):
        raise ValueError("invalid raw path")
    name = Path(path).name
    if path != str(root / "lesson_raw" / name) or not _RAW.fullmatch(name):
        raise ValueError("raw file outside lesson directory or invalid name")
    if int(_RAW.fullmatch(name).group(1)) != lesson_id:
        raise ValueError("raw file belongs to another lesson")
    from document_converter import detect_content_type
    if not detect_content_type(name):
        raise ValueError("unknown raw document extension")
    return name


def _rows(engine):
    metadata = MetaData()
    tables = {name: Table(name, metadata, autoload_with=engine) for name in REQUIRED}
    with engine.connect() as connection:
        # Never load lesson content, toolkit descriptions, batch result JSON,
        # passwords, or any other unrelated business fields into a revision.
        rows = {}
        for name, table in tables.items():
            selected = sorted(REQUIRED[name] | (set(COLUMNS.get(name, {})) & set(table.c.keys())))
            query = select(*(table.c[column] for column in selected)).order_by(table.c.id)
            rows[name] = [dict(row._mapping) for row in connection.execute(query)]
    return tables, rows


def _snapshot(app, *, allow_test_sqlite=False):
    engine = db.engine
    database = _identity(engine, allow_test_sqlite=allow_test_sqlite)
    missing, signature, blockers = _schema(engine)
    report = {"status": "dry_run", "database": database,
              "schema_columns_to_add": [f"{table}.{name}" for table, name, _ in missing],
              "counts": {"local_pending": 0, "s3_already": 0, "legacy_raw_pending": 0,
                         "legacy_raw_linked": 0, "deleted_skipped": 0},
              "anomalies": list(blockers)}
    if blockers:
        report["revision"] = None
        return report, [], []
    root = _root(app)
    tables, rows = _rows(engine)
    assets, legacy = [], []
    fingerprint = []
    assets_by_id = {row["id"]: row for row in rows["file_asset"]}
    refs = {}
    bindings = {}
    for ref in rows["file_reference"]:
        bindings[ref["binding_key"]] = ref
        if ref["usage"] == "lesson_raw":
            refs.setdefault(ref["lesson_id"], []).append(ref)
    authors = {row["id"]: row["author_id"] for row in rows["toolkit"]}
    user_ids = {row["id"] for row in rows["user"]}
    for row in sorted(rows["file_asset"], key=lambda row: row["id"]):
        asset_id = row["id"]
        backend, key = row.get("storage_backend"), row.get("object_key")
        if row["state"] == "deleted":
            report["counts"]["deleted_skipped"] += 1
            continue
        if (not safe_stored_name(row["stored_name"]) or row["purpose"] not in KINDS or
                not isinstance(row["size"], int) or row["size"] < 0 or
                (row["sha256"] is not None and not _HEX.fullmatch(row["sha256"]))):
            report["anomalies"].append(f"invalid_asset_metadata:{asset_id}")
            continue
        if backend == "s3":
            if not key or not row["sha256"]:
                report["anomalies"].append(f"invalid_s3_mapping:{asset_id}")
                continue
            try:
                storage._check_key(key)
            except ValueError:
                report["anomalies"].append(f"invalid_s3_mapping:{asset_id}")
                continue
            report["counts"]["s3_already"] += 1
        elif backend in (None, "local") and key is None:
            try:
                with _checked_file(root, row["purpose"], row["stored_name"]) as fd:
                    size, digest, identity = _fingerprint(fd)
                if size != row["size"] or (row["sha256"] and digest != row["sha256"]):
                    raise ValueError("registered size/checksum mismatch")
                assets.append((row, digest, identity))
                fingerprint.append(("asset", asset_id, size, digest, identity))
                report["counts"]["local_pending"] += 1
            except (OSError, ValueError):
                report["anomalies"].append(f"invalid_local_file:{asset_id}")
        else:
            report["anomalies"].append(f"invalid_storage_mapping:{asset_id}")
    for lesson in sorted(rows["lesson"], key=lambda row: row["id"]):
        path = lesson["raw_file_path"]
        if not path:
            continue
        lesson_id = lesson["id"]
        try:
            name = _raw_name(lesson_id, path, root)
            existing = refs.get(lesson_id, [])
            if existing:
                if (len(existing) != 1 or existing[0]["toolkit_id"] != lesson["toolkit_id"] or
                        not (asset := assets_by_id.get(existing[0]["asset_id"])) or
                        asset["purpose"] != "lesson_raw" or
                        asset["owner_id"] != authors.get(lesson["toolkit_id"])):
                    raise ValueError("conflicting lesson raw reference")
                # Even an already linked original must not hide a symlink or
                # escape; the historical path remains untouched, not ignored.
                with _checked_file(root, "lesson_raw", name) as fd:
                    size, digest, identity = _fingerprint(fd)
                fingerprint.append(("linked_raw", lesson_id, path, size, digest, identity))
                report["counts"]["legacy_raw_linked"] += 1
                continue  # The original path is deliberately never rewritten.
            binding = f"lesson:{lesson_id}:raw"
            if binding in bindings:
                raise ValueError("raw reference binding occupied")
            owner = authors.get(lesson["toolkit_id"])
            if owner is None or owner not in user_ids:
                raise ValueError("missing author")
            with _checked_file(root, "lesson_raw", name) as fd:
                size, digest, identity = _fingerprint(fd)
            legacy.append((lesson, name, owner, size, digest, identity))
            fingerprint.append(("raw", lesson_id, path, owner, size, digest, identity))
            report["counts"]["legacy_raw_pending"] += 1
        except (OSError, ValueError):
            report["anomalies"].append(f"invalid_legacy_raw:{lesson_id}")
    # Data and inode/content revisions protect against a stale approval after edits.
    # No URLs, paths, credentials, or content hashes are printed in the report.
    revision_data = (database, signature, rows, fingerprint)
    report["revision"] = hashlib.sha256(json.dumps(revision_data, sort_keys=True, default=str,
                                           ensure_ascii=True).encode()).hexdigest()
    return report, assets, legacy


def inspect_migration(app, *, allow_test_sqlite=False):
    """Read-only, deterministic dry-run; S3 is never contacted."""
    with app.app_context():
        report, _, _ = _snapshot(app, allow_test_sqlite=allow_test_sqlite)
        return report


def _require_s3(app):
    if not app.config.get("S3_BUCKET") or not app.config.get("S3_REGION"):
        raise MigrationSafetyError("S3_BUCKET and S3_REGION must be configured")
    # The live app may still be in local mode; validate S3 settings without
    # changing its mode or printing credentials/endpoint details.
    from config import build_config
    build_config({**app.config, "FILE_STORAGE_BACKEND": "s3"})


def _upload_verified(root, kind, name, size, digest, identity):
    with _checked_file(root, kind, name) as fd:
        actual_size, actual_digest, current = _fingerprint(fd)
        if (actual_size, actual_digest, current) != (size, digest, identity):
            raise ValueError("file changed since dry-run")
        key = f"files/{uuid.uuid4().hex}"
        # /proc/self/fd pins the inspected inode even if the pathname is swapped
        # during put_file's own open. No staging copy or local unlink is needed.
        storage.put_file(key, f"/proc/self/fd/{fd}", size, digest)
        storage.read_to_file(key, None, size, digest, size)
        if _fingerprint(fd) != (size, digest, identity):
            raise ValueError("file changed during upload")
        # The held inode is stable, but another process could replace the
        # pathname while it is open. Refuse to switch the database mapping if
        # the name no longer points to precisely the reviewed local bytes.
        with _checked_file(root, kind, name) as named_fd:
            if _fingerprint(named_fd) != (size, digest, identity):
                raise ValueError("local pathname changed during upload")
    return key


def apply_migration(app, *, database_name, expected_revision, backup_confirmed=False,
                    maintenance_confirmed=False, allow_test_sqlite=False):
    """Apply one resumable copy pass; a failed upload never changes its row.

    SQLite is available exclusively to direct callers with injected test apps;
    the CLI never exposes that exception to the MySQL identity requirement.
    """
    if not backup_confirmed or not maintenance_confirmed or not expected_revision or not database_name:
        raise MigrationSafetyError("Apply requires DB name, dry-run revision, backup and maintenance confirmations")
    with app.app_context():
        engine = db.engine
        actual = _identity(engine, allow_test_sqlite=allow_test_sqlite)
        if database_name != actual:
            raise MigrationSafetyError("Database identity mismatch")
        _require_s3(app)
        report, assets, legacy = _snapshot(app, allow_test_sqlite=allow_test_sqlite)
        if report["anomalies"] or not report["revision"] or report["revision"] != expected_revision:
            raise MigrationSafetyError("Dry-run revision changed or anomalies remain; review a new dry run")
        root = _root(app)
        missing, _, problems = _schema(engine)
        if problems:
            raise MigrationSafetyError("Required schema is incomplete")
        for table, column, sqltype in missing:
            # Identifiers and types come ONLY from the fixed COLUMNS constant.
            with engine.begin() as connection:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {sqltype} NULL")
        metadata = MetaData()
        asset_table = Table("file_asset", metadata, autoload_with=engine)
        ref_table = Table("file_reference", metadata, autoload_with=engine)
        lesson_table = Table("lesson", metadata, autoload_with=engine)
        toolkit_table = Table("toolkit", metadata, autoload_with=engine)
        result = {"status": "applied", "database": actual, "source_revision": expected_revision,
                  "schema_columns_added": report["schema_columns_to_add"],
                  "counts": {**report["counts"], "assets_copied": 0, "raw_registered": 0,
                             "failed": 0}, "anomalies": []}
        for row, digest, identity in assets:
            try:
                key = _upload_verified(root, row["purpose"], row["stored_name"], row["size"], digest, identity)
                with engine.begin() as connection:
                    condition = (asset_table.c.id == row["id"])
                    for name in ("stored_name", "purpose", "size", "state", "sha256", "storage_backend", "object_key"):
                        value = row.get(name)
                        condition &= asset_table.c[name].is_(None) if value is None else asset_table.c[name] == value
                    changed = connection.execute(update(asset_table).where(condition).values(
                        storage_backend="s3", object_key=key, sha256=digest)).rowcount
                    if changed != 1:
                        raise ValueError("asset row changed during upload")
                result["counts"]["assets_copied"] += 1
            except Exception as exc:
                # Keep both the original bytes and any already-uploaded private
                # object. Failed S3/SQL writes are retried on a fresh dry-run.
                result["counts"]["failed"] += 1
                result["anomalies"].append(f"asset_copy_failed:{row['id']}:{type(exc).__name__}")
        for lesson, name, owner, size, digest, identity in legacy:
            try:
                key = _upload_verified(root, "lesson_raw", name, size, digest, identity)
                suffix = Path(name).suffix.lower()
                stem = secure_filename(Path(name).stem) or "document"
                stored = f"{uuid.uuid4().hex}_{stem[:200 - len(suffix)]}{suffix}"
                if not safe_stored_name(stored):
                    raise ValueError("invalid generated stored name")
                with engine.begin() as connection:
                    current = connection.execute(select(lesson_table.c.raw_file_path, toolkit_table.c.author_id)
                        .join(toolkit_table, toolkit_table.c.id == lesson_table.c.toolkit_id)
                        .where(lesson_table.c.id == lesson["id"],
                               lesson_table.c.toolkit_id == lesson["toolkit_id"])).one_or_none()
                    if not current or current != (lesson["raw_file_path"], owner):
                        raise ValueError("lesson owner or original path changed")
                    if connection.execute(select(ref_table.c.id).where(
                            ref_table.c.lesson_id == lesson["id"], ref_table.c.usage == "lesson_raw")).first():
                        raise ValueError("raw reference appeared during upload")
                    inserted = connection.execute(asset_table.insert().values(
                        stored_name=stored, owner_id=owner, purpose="lesson_raw", size=size,
                        sha256=digest, storage_backend="s3", object_key=key,
                        state="active", is_public=False))
                    connection.execute(ref_table.insert().values(
                        asset_id=inserted.inserted_primary_key[0], toolkit_id=lesson["toolkit_id"],
                        lesson_id=lesson["id"], usage="lesson_raw",
                        binding_key=f"lesson:{lesson['id']}:raw", name=name, sort_order=0))
                result["counts"]["raw_registered"] += 1
            except Exception as exc:
                result["counts"]["failed"] += 1
                result["anomalies"].append(f"raw_copy_failed:{lesson['id']}:{type(exc).__name__}")
        if result["counts"]["failed"]:
            result["status"] = "partial"
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run-only by default, private S3 cutover")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database-name")
    parser.add_argument("--expected-revision")
    parser.add_argument("--backup-confirmed", action="store_true")
    parser.add_argument("--maintenance-confirmed", action="store_true")
    args = parser.parse_args(argv)
    try:
        from app import create_app
        app = create_app()
        if args.apply:
            report = apply_migration(app, database_name=args.database_name,
                expected_revision=args.expected_revision, backup_confirmed=args.backup_confirmed,
                maintenance_confirmed=args.maintenance_confirmed)
        else:
            report = inspect_migration(app)
            if args.database_name and args.database_name != report["database"]:
                raise MigrationSafetyError("Database identity mismatch")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if not report["anomalies"] else 2
    except Exception as exc:
        # Never render config, connection strings, driver diagnostics or paths.
        print(json.dumps({"status": "blocked", "reason": (str(exc) if isinstance(exc, MigrationSafetyError)
                          else type(exc).__name__)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
