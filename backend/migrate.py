"""Explicit, resumable schema/asset migration. Importing this module does no IO.

CLI defaults to a read-only dry run and requires an explicit database URL from
SQLALCHEMY_DATABASE_URI or DATABASE_URL, never app/.env defaults. MySQL DDL has
implicit commits: this is a journaled sequence, NOT a whole-batch transaction.
Stop application writers and make verified DB/files backups before applying.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import uuid
from urllib.parse import quote, urlsplit

from sqlalchemy import (Column, DateTime, Index, MetaData, String, Table,
                        create_engine, inspect, select, text)
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateColumn

from extensions import db
import models  # Registers metadata only; never imports the application.

TARGET_REVISION = "2026-10-safety-v1"
JOURNAL_NAME = "zhihe_schema_migration"
STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}_[A-Za-z0-9._\-]{1,200}$")
KINDS = {"image", "resource", "asset"}
CORE_TABLES = {"user", "toolkit", "lesson", "order", "purchase", "wallet_transaction", "favorite"}
FINANCIAL_COLUMNS = {
    "user": ("id", "balance", "frozen_balance"),
    "order": None, "purchase": None, "wallet_transaction": None,
}
JOURNAL = Table(JOURNAL_NAME, MetaData(),
                Column("revision", String(80), primary_key=True),
                Column("step", String(160), primary_key=True),
                Column("completed_at", DateTime, nullable=False))


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def database_identity(uri):
    url = make_url(uri)
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            raise ValueError("CLI migration requires an existing named SQLite database")
        if url.query:
            raise ValueError("SQLite URL query parameters are not accepted by migration CLI")
        path = Path(url.database)
        if not path.is_absolute():
            raise ValueError("SQLite migration database path must be absolute")
        _no_symlinks(path)
        if not path.is_file():
            raise ValueError("Create a dedicated empty SQLite file explicitly before initialization")
        return str(path.resolve())
    if url.get_backend_name() != "mysql" or not url.database:
        raise ValueError("Migration supports explicit named MySQL or SQLite databases only")
    return url.database


def open_engine(uri, readonly=True):
    """No missing SQLite file creation and no implicit real-database selection."""
    identity = database_identity(uri)
    url = make_url(uri)
    if url.get_backend_name() == "sqlite" and readonly:
        target = "file:" + quote(identity, safe="/") + "?mode=ro"
        return create_engine("sqlite://", creator=lambda: sqlite3.connect(target, uri=True))
    engine = create_engine(uri, pool_pre_ping=True)
    if readonly and url.get_backend_name() == "mysql":
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def read_only_connection(connection, _record):
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
    return engine


def _no_symlinks(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Symlink paths are not accepted")
    return path


def _safe_root(root):
    path = _no_symlinks(Path(root))
    if path.exists() and not path.is_dir():
        raise ValueError("UPLOAD_FOLDER must be a directory")
    return path.resolve()


def _file_digest(path):
    _no_symlinks(path)
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Not a regular file")
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError("File changed during inspection")
    return digest.hexdigest(), before.st_size


def financial_snapshot(connection):
    """Read-only hashes of every historical money/order/entitlement record."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    result = {}
    for name, chosen in FINANCIAL_COLUMNS.items():
        if name not in tables:
            continue
        table = Table(name, MetaData(), autoload_with=connection)
        columns = [column for column in table.columns if column.name != "event_key"] if chosen is None else [table.c[key] for key in chosen if key in table.c]
        rows = [list(row) for row in connection.execute(select(*columns).order_by(table.c.id))]
        result[name] = {"rows": len(rows), "sha256": _hash(rows)}
    return result


def _anomalies(connection, tables):
    result = {}
    queries = {
        "negative_balances": ('SELECT id FROM user WHERE balance < 0 OR frozen_balance < 0', {"user"}),
        "nonpositive_orders": ('SELECT id FROM `order` WHERE amount <= 0', {"order"}),
        "duplicate_pending": ('SELECT buyer_id, toolkit_id, COUNT(*) FROM `order` WHERE status = \'pending\' GROUP BY buyer_id, toolkit_id HAVING COUNT(*) > 1', {"order"}),
        "paid_without_purchase": ('SELECT o.id FROM `order` o LEFT JOIN purchase p ON p.order_id=o.id WHERE o.status=\'paid\' AND p.id IS NULL', {"order", "purchase"}),
        "purchase_mismatch": ('SELECT p.id FROM purchase p LEFT JOIN `order` o ON o.id=p.order_id WHERE o.id IS NULL OR o.status<>\'paid\' OR p.user_id<>o.buyer_id OR p.toolkit_id<>o.toolkit_id', {"order", "purchase"}),
    }
    for name, (query, required) in queries.items():
        if required <= tables:
            rows = [list(row) for row in connection.execute(text(query))]
            result[name] = {"count": len(rows), "ids": rows}
    return result


def _schema_plan(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    # The marker is owned by the isolated MySQL fixture, not a business table.
    business_tables = tables - {JOURNAL_NAME, "__zhihe_test_marker"}
    empty = not business_tables
    if JOURNAL_NAME in tables and business_tables <= set(db.metadata.tables):
        initializing = connection.execute(select(JOURNAL.c.step).where(
            (JOURNAL.c.revision == TARGET_REVISION) & (JOURNAL.c.step == "initialize-empty"))).first()
        if initializing:
            empty = all(not connection.execute(select(Table(name, MetaData(), autoload_with=connection)).limit(1)).first()
                        for name in business_tables)
    steps, blockers, schema = [], [], {}
    if not empty and not CORE_TABLES <= tables:
        blockers.append("Partial/unknown legacy schema: missing core tables; initialize-empty is not an upgrade")
    for table in db.metadata.sorted_tables:
        if table.name not in tables:
            steps.append({"id": "table:" + table.name, "action": "create_table", "table": table.name})
            continue
        columns = inspector.get_columns(table.name)
        indexes = inspector.get_indexes(table.name)
        uniques = inspector.get_unique_constraints(table.name)
        schema[table.name] = {
            "columns": [{"name": col["name"], "type": str(col["type"]), "nullable": col["nullable"], "default": str(col.get("default"))} for col in columns],
            "indexes": sorted([{"name": idx["name"], "columns": idx["column_names"], "unique": bool(idx.get("unique"))} for idx in indexes], key=lambda item: item["name"] or ""),
            "unique": sorted([{"name": idx["name"], "columns": idx["column_names"]} for idx in uniques], key=lambda item: item["name"] or ""),
        }
        by_name = {col["name"]: col for col in columns}
        supported = {("user", "token_version"), ("wallet_transaction", "event_key")}
        for col in table.columns:
            if col.name not in by_name:
                if (table.name, col.name) in supported:
                    steps.append({"id": f"column:{table.name}:{col.name}", "action": "add_column", "table": table.name, "column": col.name})
                else:
                    blockers.append(f"Unsupported missing column {table.name}.{col.name}; needs a reviewed migration")
        if table.name == "user" and "token_version" in by_name:
            col = by_name["token_version"]
            if col["nullable"] or not isinstance(col["type"], models.db.Integer):
                blockers.append("Existing user.token_version must be NOT NULL integer; no automatic historical repair")
            default = str(col.get("default") or "").strip(" ()'\"")
            if default != "0":
                if connection.dialect.name == "mysql":
                    steps.append({"id": "default:user:token_version", "action": "token_default"})
                else:
                    blockers.append("Existing user.token_version requires DEFAULT 0; SQLite rebuild needs a separate reviewed migration")
        if table.name == "wallet_transaction" and "event_key" in by_name and not by_name["event_key"]["nullable"]:
            blockers.append("Historical wallet_transaction.event_key must remain nullable")
        if table.name == "order":
            wanted = ["buyer_id", "toolkit_id", "status"]
            if any(idx["column_names"] == wanted for idx in uniques) or any(idx.get("unique") and idx["column_names"] == wanted for idx in indexes):
                blockers.append("Existing unique order buyer/toolkit/status constraint requires manual review; never delete historical pending orders")
            if not any(not idx.get("unique") and idx["column_names"] == wanted for idx in indexes):
                if any(idx["name"] == "ix_order_buyer_toolkit_status" for idx in indexes):
                    blockers.append("Order index name conflicts with a different index")
                else:
                    steps.append({"id": "index:order:buyer_toolkit_status", "action": "order_index"})
        if table.name == "wallet_transaction":
            if not any(idx["column_names"] == ["event_key"] for idx in uniques) and not any(idx.get("unique") and idx["column_names"] == ["event_key"] for idx in indexes):
                if "event_key" in by_name:
                    duplicates = connection.execute(text("SELECT event_key FROM wallet_transaction WHERE event_key IS NOT NULL GROUP BY event_key HAVING COUNT(*)>1")).first()
                    if duplicates:
                        blockers.append("Duplicate non-null event keys; financial rows will not be rewritten")
                steps.append({"id": "index:wallet_transaction:event_key", "action": "event_index"})
        if connection.dialect.name == "mysql":
            for field in ({"toolkit": ["description"], "lesson": ["content"]}.get(table.name, [])):
                if field in by_name and str(by_name[field]["type"]).upper() != "LONGTEXT":
                    steps.append({"id": f"longtext:{table.name}:{field}", "action": "longtext", "table": table.name, "column": field, "nullable": by_name[field]["nullable"]})
            enum_field = {"order": "status", "wallet_transaction": "type"}.get(table.name)
            if enum_field and enum_field in by_name:
                current = getattr(by_name[enum_field]["type"], "enums", None)
                required = list(table.c[enum_field].type.enums)
                if current is None:
                    blockers.append(f"Existing {table.name}.{enum_field} is not an ENUM; needs explicit review")
                elif not set(required) <= set(current):
                    steps.append({"id": f"enum:{table.name}:{enum_field}", "action": "enum", "table": table.name, "column": enum_field,
                                  "values": list(dict.fromkeys([*current, *required])), "nullable": by_name[enum_field]["nullable"]})
    return steps, blockers, schema, empty, tables


def _parse_reference(value):
    if not isinstance(value, str) or not value:
        return None
    if value.startswith(("http://", "https://")):
        return "external"
    if value.startswith("file:"):
        value = value[5:]
    elif value.startswith("/api/uploads/"):
        value = value[len("/api/uploads/"):]
    elif value.startswith("/api/lesson-asset/"):
        value = value[len("/api/lesson-asset/"):]
    if "\\" in value or "%" in value or "\x00" in value or "?" in value or "#" in value:
        raise ValueError("Invalid local reference")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise ValueError("Invalid local reference")
    if len(parts) == 1:
        return value
    if len(parts) == 2 and parts[0] in KINDS:
        return value
    raise ValueError("Reference is outside approved upload locations")


def _references(connection, tables):
    refs = []
    if "toolkit" not in tables or "lesson" not in tables:
        return refs
    for row in connection.execute(text("SELECT id, author_id, cover, resource_path FROM toolkit ORDER BY id")).mappings():
        for field, usage, kind in [("cover", "cover", "image"), ("resource_path", "resource", "resource")]:
            if row[field]:
                refs.append({"binding": f"toolkit:{row['id']}:{usage}", "table": "toolkit", "id": row["id"], "toolkit_id": row["id"], "lesson_id": None,
                             "author_id": row["author_id"], "column": field, "value": row[field], "usage": usage, "kind": kind})
    for row in connection.execute(text("SELECT l.id, l.toolkit_id, t.author_id, l.image FROM lesson l LEFT JOIN toolkit t ON t.id=l.toolkit_id ORDER BY l.id")).mappings():
        if row["image"]:
            refs.append({"binding": f"lesson:{row['id']}:image", "table": "lesson", "id": row["id"], "toolkit_id": row["toolkit_id"], "lesson_id": row["id"],
                         "author_id": row["author_id"], "column": "image", "value": row["image"], "usage": "lesson", "kind": "asset"})
    return refs


def _asset_plan(connection, root, tables):
    groups, issues, used, refs = {}, [], set(), _references(connection, tables)
    registry = {}
    operations = set()
    if "file_asset" in tables:
        registry = {row["stored_name"]: dict(row) for row in connection.execute(text("SELECT * FROM file_asset")).mappings()}
    if "file_operation" in tables:
        operations = {row[0] for row in connection.execute(text("SELECT id FROM file_operation WHERE action='migration_copy'"))}
    bindings = {}
    if "file_reference" in tables:
        bindings = {row["binding_key"]: dict(row) for row in connection.execute(text("SELECT * FROM file_reference")).mappings()}
    for ref in refs:
        try:
            parsed = _parse_reference(ref["value"])
        except ValueError:
            issues.append({"binding": ref["binding"], "reason": "invalid_path"})
            continue
        if parsed == "external" or parsed is None:
            continue
        ref["basename"] = Path(parsed).name
        candidates = [root / parsed] if "/" in parsed else [root / kind / parsed for kind in sorted(KINDS)] + [root / parsed]
        existing, unsafe = [], False
        for path in candidates:
            try:
                _no_symlinks(path)
            except ValueError:
                unsafe = True
                continue
            if path.is_file():
                existing.append(path)
        if unsafe or len(existing) != 1:
            reason = "symlink" if unsafe else "missing" if not existing else "ambiguous_source"
            issues.append({"binding": ref["binding"], "name": ref["basename"], "reason": reason})
            continue
        source = existing[0]
        relative = source.relative_to(root).as_posix()
        used.add(relative)
        groups.setdefault(relative, {"source": relative, "refs": []})["refs"].append(ref)
    assets = []
    for relative, group in sorted(groups.items()):
        references = group["refs"]
        kinds = {ref["kind"] for ref in references}
        owners = {ref["author_id"] for ref in references}
        name = Path(relative).name
        group["name"] = name
        try:
            digest, size = _file_digest(root / relative)
        except (ValueError, OSError):
            issues.append({"source": relative, "name": name, "reason": "unreadable_or_changed"})
            continue
        group.update({"sha256": digest, "size": size})
        if len(kinds) != 1 or len(owners) != 1 or None in owners:
            group["decision"] = "retain_conflict"
            group["reason"] = "purpose_conflict" if len(kinds) != 1 else "shared_owner_unknown"
            assets.append(group)
            continue
        kind = next(iter(kinds))
        group["kind"] = kind
        if relative == f"{kind}/{name}" and STORED_NAME_RE.fullmatch(name):
            stored = name
        else:
            safe_name = re.sub(r"[^A-Za-z0-9._\-]", "_", name).lstrip(".") or "file"
            stored = _hash([TARGET_REVISION, relative, kind, digest])[:32] + "_" + safe_name[:200]
        group["stored_name"] = stored
        group["target"] = f"{kind}/{stored}"
        group["operation_id"] = _hash([TARGET_REVISION, relative, kind, digest, "copy"])[:32]
        target = root / group["target"]
        old = registry.get(stored)
        try:
            _no_symlinks(target)
            if target.exists() and target != root / relative:
                if not (old and old["sha256"] == digest and group["operation_id"] in operations and _file_digest(target) == (digest, size)):
                    raise ValueError("Unowned destination conflict")
            if old and (old["purpose"] != kind or old["sha256"] not in {None, digest} or old["state"] not in {"legacy", "staging", "active", "retained"}):
                raise ValueError("Registry destination conflict")
            if old:
                for ref in references:
                    previous = bindings.get(ref["binding"])
                    if previous and previous["asset_id"] != old["id"]:
                        raise ValueError("Binding conflict")
            elif any(ref["binding"] in bindings for ref in references):
                raise ValueError("Binding conflict")
        except (ValueError, OSError):
            group["decision"], group["reason"] = "retain_conflict", "target_or_registry_conflict"
        else:
            group["decision"] = "register" if group["source"] == group["target"] else "copy_and_register"
        assets.append(group)
    # The registry key is global across directories. Do not guess when two
    # physical files have the same advertised name, even if their bytes match.
    by_name = {}
    for group in assets:
        by_name.setdefault(group["name"], []).append(group)
    for groups_with_name in by_name.values():
        if len(groups_with_name) > 1:
            for group in groups_with_name:
                group["decision"], group["reason"] = "retain_conflict", "duplicate_stored_name"
    orphans = []
    if root.exists():
        for directory in [root, *(root / kind for kind in sorted(KINDS))]:
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                relative = path.relative_to(root).as_posix()
                if relative not in used and (path.is_file() or path.is_symlink()):
                    orphans.append({"path": relative, "reason": "symlink" if path.is_symlink() else "unreferenced_retained"})
    return assets, issues, orphans


def inspect_migration(engine, upload_folder):
    root = _safe_root(upload_folder)
    with engine.connect() as connection:
        steps, blockers, schema, empty, tables = _schema_plan(connection)
        assets, issues, orphans = _asset_plan(connection, root, tables)
        financial = financial_snapshot(connection)
        report = {"target_revision": TARGET_REVISION, "upload_folder": str(root), "empty_database": empty,
                  "schema_steps": steps, "blockers": blockers, "assets": assets,
                  "asset_issues": issues, "orphans_retained": orphans,
                  "financial_snapshot": financial, "anomalies_report_only": _anomalies(connection, tables),
                  "mysql_ddl_implicit_commit": engine.dialect.name == "mysql"}
        # Include existing schema/registry state so apply cannot accept a stale
        # review simply because there are no currently pending DDL steps.
        registry_state = {}
        for name in ["file_asset", "file_reference", "file_operation", JOURNAL_NAME]:
            if name in tables:
                table = Table(name, MetaData(), autoload_with=connection)
                registry_state[name] = [list(row) for row in connection.execute(select(table).order_by(*table.primary_key.columns))]
        report["revision"] = _hash([report, schema, registry_state])
    return report


def _journal_step(connection, step):
    condition = (JOURNAL.c.revision == TARGET_REVISION) & (JOURNAL.c.step == step)
    if not connection.execute(select(JOURNAL.c.step).where(condition)).first():
        connection.execute(JOURNAL.insert().values(revision=TARGET_REVISION, step=step, completed_at=datetime.now(timezone.utc).replace(tzinfo=None)))


def _apply_schema_step(engine, step):
    with engine.begin() as connection:
        action = step["action"]
        if action == "create_table":
            db.metadata.tables[step["table"]].create(connection, checkfirst=True)
        elif action == "add_column":
            table = db.metadata.tables[step["table"]]
            rendered = str(CreateColumn(table.c[step["column"]]).compile(dialect=connection.dialect))
            connection.execute(text(f"ALTER TABLE {connection.dialect.identifier_preparer.quote(table.name)} ADD COLUMN {rendered}"))
        elif action == "token_default":
            connection.execute(text("ALTER TABLE `user` ALTER COLUMN `token_version` SET DEFAULT 0"))
        elif action == "order_index":
            next(idx for idx in models.Order.__table__.indexes if idx.name == "ix_order_buyer_toolkit_status").create(connection)
        elif action == "event_index":
            reflected = Table("wallet_transaction", MetaData(), autoload_with=connection)
            Index("uq_wallet_transaction_event_key", reflected.c.event_key, unique=True).create(connection)
        elif action in {"longtext", "enum"}:
            table, column = step["table"], step["column"]
            nullable = "NULL" if step["nullable"] else "NOT NULL"
            if action == "longtext":
                sql_type = "LONGTEXT"
            else:
                # Values originate from reflected/model enums, but quote them
                # explicitly instead of interpolating an operator input.
                values = ["'" + value.replace("\\", "\\\\").replace("'", "''") + "'" for value in step["values"]]
                sql_type = "ENUM(" + ",".join(values) + ")"
            connection.execute(text(f"ALTER TABLE `{table}` MODIFY `{column}` {sql_type} {nullable}"))
        _journal_step(connection, step["id"])


def _block_old_name(connection, name):
    if not STORED_NAME_RE.fullmatch(name):
        return
    asset = models.FileAsset.__table__
    old = connection.execute(select(asset).where(asset.c.stored_name == name)).mappings().first()
    if old:
        # Never mutate an owned/new upload based on an ambiguous historical URL.
        if old["owner_id"] is not None or old["state"] not in {"legacy", "staging"}:
            raise ValueError("Cannot quarantine an owned registry entry; manual review required")
        if old["is_public"]:
            connection.execute(asset.update().where(asset.c.id == old["id"]).values(is_public=False))
    else:
        connection.execute(asset.insert().values(stored_name=name, owner_id=None, purpose="asset", size=0,
                                                sha256=None, state="legacy", is_public=False))


def _copy_retaining_source(root, group):
    source, target = root / group["source"], root / group["target"]
    _no_symlinks(source)
    _no_symlinks(target)
    if _file_digest(source) != (group["sha256"], group["size"]):
        raise ValueError("Source changed after dry-run")
    if target.exists():
        if _file_digest(target) != (group["sha256"], group["size"]):
            raise ValueError("Destination changed after dry-run")
        return  # Only journal-owned, digest-matched targets reach this branch.
    _no_symlinks(root / ".staging")
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / ".staging").mkdir(exist_ok=True)
    staging = root / ".staging" / (group["operation_id"] + "_" + uuid.uuid4().hex)
    # If interrupted, a staging copy may remain. No sweeping/deletion is done.
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(source_fd, "rb") as incoming, staging.open("xb") as outgoing:
        digest, total = hashlib.sha256(), 0
        if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
            raise ValueError("Source is not regular")
        for block in iter(lambda: incoming.read(128 * 1024), b""):
            total += len(block)
            if total > group["size"]:
                raise ValueError("Source grew during copy")
            digest.update(block)
            outgoing.write(block)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if (digest.hexdigest(), total) != (group["sha256"], group["size"]):
        raise ValueError("Source changed during copy; original/staging retained")
    # Hard link is an atomic exclusive publish on the same upload filesystem;
    # unlike replace/rename it cannot overwrite an unrelated target.
    os.link(staging, target, follow_symlinks=False)
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    staging.unlink()  # Only this invocation's exact staging file, never source.


def _apply_asset(engine, root, group):
    if group["decision"] == "retain_conflict":
        with engine.begin() as connection:
            _block_old_name(connection, group["name"])
            _journal_step(connection, "quarantine:" + _hash(group["source"])[:32])
        return
    asset, reference, operation = models.FileAsset.__table__, models.FileReference.__table__, models.FileOperation.__table__
    copied = group["decision"] == "copy_and_register"
    with engine.begin() as connection:
        existing = connection.execute(select(asset).where(asset.c.stored_name == group["stored_name"])).mappings().first()
        if existing:
            asset_id = existing["id"]
        else:
            asset_id = connection.execute(asset.insert().values(stored_name=group["stored_name"], owner_id=None, purpose=group["kind"],
                         size=group["size"], sha256=group["sha256"], state="staging" if copied else "legacy", is_public=False)).inserted_primary_key[0]
        if copied and not connection.execute(select(operation.c.id).where(operation.c.id == group["operation_id"])).first():
            connection.execute(operation.insert().values(id=group["operation_id"], asset_id=asset_id, action="migration_copy", status="pending"))
        if copied:
            _block_old_name(connection, group["name"])
    if copied:
        _copy_retaining_source(root, group)
    elif _file_digest(root / group["source"]) != (group["sha256"], group["size"]):
        raise ValueError("Source changed after review")
    with engine.begin() as connection:
        for ref in group["refs"]:
            table = db.metadata.tables[ref["table"]]
            current = connection.execute(select(table.c[ref["column"]]).where(table.c.id == ref["id"])).scalar_one()
            new_value = ("/api/uploads/" + group["stored_name"] if ref["usage"] == "cover" else
                         "file:" + group["stored_name"] if ref["usage"] == "lesson" else group["stored_name"])
            if current not in {ref["value"], new_value}:
                raise ValueError("Reference changed during offline migration")
            if current != new_value:
                # Update only the binding, without model updated_at hooks or
                # incidental historical metadata changes on replay.
                connection.execute(text(f"UPDATE `{ref['table']}` SET `{ref['column']}`=:value WHERE id=:id"),
                                   {"value": new_value, "id": ref["id"]})
            existing_ref = connection.execute(select(reference).where(reference.c.binding_key == ref["binding"])).mappings().first()
            if existing_ref and existing_ref["asset_id"] != asset_id:
                raise ValueError("Existing file binding points to a different asset")
            if not existing_ref:
                connection.execute(reference.insert().values(asset_id=asset_id, toolkit_id=ref["toolkit_id"], lesson_id=ref["lesson_id"], usage=ref["usage"], binding_key=ref["binding"]))
        existing = connection.execute(select(asset).where(asset.c.id == asset_id)).mappings().one()
        updates = {"sha256": group["sha256"], "size": group["size"], "is_public": group["kind"] == "image"}
        if existing["state"] == "staging":
            updates["state"] = "legacy"
        updates = {key: value for key, value in updates.items() if existing[key] != value}
        if updates:
            connection.execute(asset.update().where(asset.c.id == asset_id).values(**updates))
        if copied:
            connection.execute(operation.update().where((operation.c.id == group["operation_id"]) &
                                                         (operation.c.status != "committed")).values(status="committed"))
        _journal_step(connection, "asset:" + _hash(group["target"])[:32])


def apply_migration(engine, upload_folder, *, expected_revision, backup_confirmed=False,
                    initialize_empty=False, maintenance_confirmed=False):
    """Apply a freshly inspected plan; callers supply a deliberately selected engine."""
    report = inspect_migration(engine, upload_folder)
    if report["revision"] != expected_revision:
        raise ValueError("Reviewed revision no longer matches; rerun dry-run before applying")
    if report["blockers"]:
        raise ValueError("Schema blockers require manual review; nothing applied")
    if not maintenance_confirmed:
        raise ValueError("Confirm application writers are stopped before migration")
    if initialize_empty:
        if not report["empty_database"]:
            raise ValueError("initialize-empty cannot be used for an existing business schema")
    elif not backup_confirmed:
        raise ValueError("Existing databases require a verified database/files backup confirmation")
    root = _safe_root(upload_folder)
    before = report["financial_snapshot"]
    with engine.begin() as connection:
        JOURNAL.create(connection, checkfirst=True)
        if initialize_empty:
            _journal_step(connection, "initialize-empty")
    for step in report["schema_steps"]:
        # Inspection, not journal alone, determines what still needs to run.
        # A MySQL DDL may commit before its journal record after interruption.
        pending, blockers, *_ = _schema_plan_for_engine(engine)
        if blockers and not (initialize_empty and all(message.startswith("Partial/unknown legacy schema:") for message in blockers)):
            raise ValueError("Schema changed or became incompatible during migration")
        if any(item["id"] == step["id"] for item in pending):
            _apply_schema_step(engine, step)
        else:
            with engine.begin() as connection:
                _journal_step(connection, step["id"])
    for group in report["assets"]:
        _apply_asset(engine, root, group)
    # Unsafe/missing legacy names are denied publicly; they are not deleted,
    # copied from symlinks, assigned owners, or silently rewritten.
    with engine.begin() as connection:
        for issue in report["asset_issues"]:
            if issue.get("name"):
                _block_old_name(connection, issue["name"])
    with engine.connect() as connection:
        after = financial_snapshot(connection)
    # Empty initialization creates empty financial tables, not money records.
    for table, snapshot in before.items():
        if after.get(table) != snapshot:
            raise RuntimeError("Historical financial snapshot changed; stop and investigate (DDL is not globally rollbackable)")
    with engine.begin() as connection:
        _journal_step(connection, "complete")
    return {"target_revision": TARGET_REVISION, "applied": True, "financial_unchanged": True,
            "asset_conflicts_retained": sum(group["decision"] == "retain_conflict" for group in report["assets"]),
            "asset_issues_retained": len(report["asset_issues"]), "orphans_retained": len(report["orphans_retained"])}


def _schema_plan_for_engine(engine):
    with engine.connect() as connection:
        return _schema_plan(connection)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read-only default; print a review revision")
    mode.add_argument("--apply", action="store_true", help="Apply explicitly reviewed changes")
    parser.add_argument("--expected-database", help="Exact MySQL database name or absolute SQLite file path")
    parser.add_argument("--expected-revision", help="Exact revision hash from a fresh dry-run")
    parser.add_argument("--backup-confirmed", action="store_true", help="Verified DB and original uploads backups exist")
    parser.add_argument("--initialize-empty", action="store_true", help="Explicitly initialize an empty dedicated database only")
    parser.add_argument("--maintenance-confirmed", action="store_true", help="Application writers are stopped (offline migration)")
    args = parser.parse_args(argv)
    engine = None
    try:
        uri = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get("DATABASE_URL")
        uploads = os.environ.get("UPLOAD_FOLDER")
        if not uri or not uploads:
            raise ValueError("Explicit database URL and UPLOAD_FOLDER environment variables are required; no .env/defaults are read")
        identity = database_identity(uri)
        if args.expected_database and args.expected_database != identity:
            raise ValueError("expected-database does not match the explicit database URL")
        if args.apply and (not args.expected_database or not args.expected_revision):
            raise ValueError("apply requires expected-database and expected-revision")
        if args.apply and (not args.maintenance_confirmed or not (args.backup_confirmed or args.initialize_empty)):
            raise ValueError("apply requires maintenance and backup confirmation (or explicit empty initialization)")
        engine = open_engine(uri, readonly=not args.apply)
        if args.apply:
            report = apply_migration(engine, uploads, expected_revision=args.expected_revision, backup_confirmed=args.backup_confirmed,
                                     initialize_empty=args.initialize_empty, maintenance_confirmed=args.maintenance_confirmed)
        else:
            report = inspect_migration(engine, uploads)
            report["database"] = identity
            report["mode"] = "read-only dry-run"
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    except (ValueError, RuntimeError) as error:
        # These are our own fixed operator messages; never print SQL/URL errors.
        print(f"Migration refused: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Migration failed ({type(error).__name__}); credentials/SQL omitted. Review partial DDL and rerun dry-run.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
