"""Migrations use ONLY disposable legacy schemas and generated local files."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import MetaData, String, Text, create_engine, inspect, select, text

import migrate
from extensions import db
import models
from test_support import IsolatedMySQL

BACKEND = Path(__file__).resolve().parents[1]


def legacy_schema(engine):
    metadata = MetaData()
    for table in db.metadata.sorted_tables:
        if table.name in migrate.CORE_TABLES:
            table.to_metadata(metadata)
    metadata.tables["user"]._columns.remove(metadata.tables["user"].c.token_version)
    wallet = metadata.tables["wallet_transaction"]
    for constraint in list(wallet.constraints):
        if any(column.name == "event_key" for column in constraint.columns):
            wallet.constraints.remove(constraint)
    wallet._columns.remove(wallet.c.event_key)
    order = metadata.tables["order"]
    order.indexes.clear()
    metadata.tables["toolkit"].c.description.type = Text()
    metadata.tables["lesson"].c.content.type = Text()
    metadata.create_all(engine)
    with engine.begin() as connection:
        for uid in [101, 202, 303]:
            connection.execute(metadata.tables["user"].insert().values(
                id=uid, username=f"fixture-{uid}", nickname=f"Fixture {uid}", password_hash="generated fixture placeholder",
                role="user", balance=-2 if uid == 101 else 100, frozen_balance=0))
        connection.execute(metadata.tables["toolkit"].insert().values(
            id=701, title="Fixture toolkit", slug="fixture-toolkit", summary="Synthetic migration fixture", description="fixture",
            category="生信分析", level="入门", price=10, author_id=202, status="published"))
        connection.execute(metadata.tables["lesson"].insert().values(id=801, toolkit_id=701, title="Fixture lesson", content="generated text", is_preview=False))
        for oid, status in [(901, "pending"), (902, "pending"), (903, "refunded"), (904, "paid")]:
            connection.execute(order.insert().values(id=oid, order_no=f"fixture-order-{oid}", buyer_id=101, seller_id=202,
                toolkit_id=701, amount=10, commission=2, status=status))
        connection.execute(metadata.tables["purchase"].insert().values(id=1001, user_id=101, toolkit_id=701, order_id=903))
        connection.execute(wallet.insert().values(id=1101, user_id=101, type="refund", amount=10, balance_after=-2, order_id=903, note="historical synthetic refund"))
    return metadata


@pytest.fixture
def migration_fixture(tmp_path):
    database = tmp_path / "legacy.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    legacy_schema(engine)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    yield engine, uploads, database
    engine.dispose()


def review_and_apply(engine, uploads, **kwargs):
    report = migrate.inspect_migration(engine, uploads)
    assert not report["blockers"]
    result = migrate.apply_migration(engine, uploads, expected_revision=report["revision"],
                                    backup_confirmed=True, maintenance_confirmed=True, **kwargs)
    assert result["financial_unchanged"] is True
    return report


def set_reference(engine, column, value):
    with engine.begin() as connection:
        table = "lesson" if column == "image" else "toolkit"
        connection.execute(text(f"UPDATE {table} SET {column}=:value"), {"value": value})


def tree_snapshot(root):
    return {path.relative_to(root).as_posix(): (path.lstat().st_size, path.lstat().st_mtime_ns,
            None if path.is_symlink() or path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob("*")}


def test_no_import_database_or_filesystem_writes(tmp_path):
    code = """
import os, sqlite3, sqlalchemy
from pathlib import Path
from unittest.mock import patch

def forbidden(*args, **kwargs):
    raise AssertionError('import attempted a write or database connection')
with patch.object(sqlalchemy, 'create_engine', forbidden), patch.object(sqlite3, 'connect', forbidden), patch.object(Path, 'mkdir', forbidden), patch.object(os, 'makedirs', forbidden):
    import migrate
    import static_spa
    import wsgi
assert 'app' not in vars(migrate)
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path,
                            env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(BACKEND)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_dry_run_is_read_only_and_private_svg_never_becomes_image(migration_fixture):
    engine, uploads, database = migration_fixture
    name = "a" * 32 + "_paid.svg"
    (uploads / name).write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>synthetic paid attachment</text></svg>')
    set_reference(engine, "resource_path", name)
    before_db, before_tree = database.read_bytes(), tree_snapshot(uploads)
    readonly = migrate.open_engine(str(engine.url), readonly=True)
    try:
        report = migrate.inspect_migration(readonly, uploads)
        with pytest.raises(Exception):
            with readonly.begin() as connection:
                connection.execute(text("CREATE TABLE dryrun_must_not_write (id INTEGER)"))
    finally:
        readonly.dispose()
    assert database.read_bytes() == before_db
    assert tree_snapshot(uploads) == before_tree
    assert report["assets"][0]["kind"] == "resource"
    assert report["assets"][0]["target"].startswith("resource/")
    assert report["anomalies_report_only"]["negative_balances"]["count"] == 1
    assert report["anomalies_report_only"]["duplicate_pending"]["count"] == 1
    assert report["anomalies_report_only"]["purchase_mismatch"]["count"] == 1
    assert report["anomalies_report_only"]["paid_without_purchase"]["count"] == 1
    migrate.apply_migration(engine, uploads, expected_revision=report["revision"], backup_confirmed=True, maintenance_confirmed=True)
    assert (uploads / name).exists(), "original must be retained"
    target = uploads / report["assets"][0]["target"]
    assert target.read_bytes() == (uploads / name).read_bytes()
    with engine.connect() as connection:
        rows = connection.execute(select(models.FileAsset.__table__)).mappings().all()
        assert all(not row["is_public"] for row in rows)
        migrated = next(row for row in rows if row["stored_name"] != name)
        assert migrated["purpose"] == "resource" and migrated["owner_id"] is None and migrated["state"] == "legacy"
        assert connection.execute(text("SELECT resource_path FROM toolkit")).scalar_one() == target.name
        assert connection.execute(text("SELECT binding_key FROM file_reference")).scalar_one() == "toolkit:701:resource"
        assert migrate.financial_snapshot(connection) == report["financial_snapshot"]
        assert connection.execute(text("SELECT token_version FROM user")).scalars().all() == [0, 0, 0]
    before = tree_snapshot(uploads)
    replay = review_and_apply(engine, uploads)
    assert migrate.inspect_migration(engine, uploads)["revision"] == replay["revision"]
    assert tree_snapshot(uploads) == before
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM file_reference")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM file_operation")).scalar_one() == 1


def test_correct_directory_registers_original_and_protected_lesson(migration_fixture):
    engine, uploads, _database = migration_fixture
    name = "b" * 32 + "_private.png"
    (uploads / "asset").mkdir()
    (uploads / "asset" / name).write_bytes(b"synthetic historical bytes; migration does not revalidate or delete")
    set_reference(engine, "image", "file:" + name)
    report = review_and_apply(engine, uploads)
    assert report["assets"][0]["decision"] == "register"
    with engine.connect() as connection:
        asset = connection.execute(select(models.FileAsset.__table__)).mappings().one()
        assert asset["stored_name"] == name and asset["purpose"] == "asset" and not asset["is_public"]
        assert connection.execute(text("SELECT binding_key FROM file_reference")).scalar_one() == "lesson:801:image"
    assert not (uploads / ".staging").exists()


def test_cover_registration_is_only_public_use(migration_fixture):
    engine, uploads, _database = migration_fixture
    name = "c" * 32 + "_cover.svg"
    (uploads / "image").mkdir()
    (uploads / "image" / name).write_text("<svg>synthetic historic SVG retained verbatim</svg>")
    set_reference(engine, "cover", "/api/uploads/" + name)
    review_and_apply(engine, uploads)
    with engine.connect() as connection:
        asset = connection.execute(select(models.FileAsset.__table__)).mappings().one()
        assert asset["purpose"] == "image" and asset["is_public"] and asset["owner_id"] is None


@pytest.mark.parametrize("conflict", ["purpose", "shared_author"])
def test_shared_or_purpose_conflicts_are_quarantined_not_guessed(migration_fixture, conflict):
    engine, uploads, _database = migration_fixture
    name = "d" * 32 + "_shared.svg"
    (uploads / "image").mkdir()
    source = uploads / "image" / name
    source.write_text("<svg>synthetic shared file</svg>")
    set_reference(engine, "cover", "/api/uploads/" + name)
    if conflict == "purpose":
        set_reference(engine, "resource_path", name)
    else:
        with engine.begin() as connection:
            connection.execute(models.Toolkit.__table__.insert().values(id=702, title="Other fixture", slug="other-fixture", summary="fixture", description="fixture",
                category="生信分析", price=10, author_id=303, status="published", cover="/api/uploads/" + name))
    before = tree_snapshot(uploads)
    report = review_and_apply(engine, uploads)
    assert report["assets"][0]["decision"] == "retain_conflict"
    assert tree_snapshot(uploads) == before
    with engine.connect() as connection:
        assert connection.execute(text("SELECT cover FROM toolkit WHERE id=701")).scalar_one() == "/api/uploads/" + name
        asset = connection.execute(select(models.FileAsset.__table__)).mappings().one()
        assert asset["owner_id"] is None and not asset["is_public"]
        assert connection.execute(text("SELECT COUNT(*) FROM file_reference")).scalar_one() == 0
        assert migrate.financial_snapshot(connection) == report["financial_snapshot"]
    review_and_apply(engine, uploads)


def test_symlink_missing_path_traversal_and_orphan_only_report(migration_fixture, tmp_path):
    engine, uploads, _database = migration_fixture
    name = "e" * 32 + "_link.svg"
    outside = tmp_path / "outside-secret-fixture.svg"
    outside.write_bytes(b"synthetic not-read-through-link")
    (uploads / name).symlink_to(outside)
    (uploads / "orphan.png").write_bytes(b"orphan synthetic attachment retained")
    set_reference(engine, "cover", "/api/uploads/" + name)
    set_reference(engine, "resource_path", "f" * 32 + "_missing.zip")
    set_reference(engine, "image", "file:../outside-secret-fixture.svg")
    before = tree_snapshot(uploads)
    report = review_and_apply(engine, uploads)
    assert {issue["reason"] for issue in report["asset_issues"]} == {"symlink", "missing", "invalid_path"}
    assert report["orphans_retained"]
    assert tree_snapshot(uploads) == before
    assert outside.read_bytes() == b"synthetic not-read-through-link"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM file_reference")).scalar_one() == 0
        assert connection.execute(text("SELECT SUM(CASE WHEN is_public THEN 1 ELSE 0 END) FROM file_asset")).scalar_one() == 0


def test_destination_conflict_preserves_all_bytes(migration_fixture):
    engine, uploads, _database = migration_fixture
    name = "a" * 32 + "_paid.svg"
    (uploads / name).write_bytes(b"synthetic private source")
    set_reference(engine, "resource_path", name)
    original = migrate.inspect_migration(engine, uploads)
    target = uploads / original["assets"][0]["target"]
    target.parent.mkdir()
    target.write_bytes(b"unrelated destination must not be replaced")
    before = tree_snapshot(uploads)
    report = review_and_apply(engine, uploads)
    assert report["assets"][0]["decision"] == "retain_conflict"
    assert tree_snapshot(uploads) == before


def test_resume_after_copy_before_reference_commit(migration_fixture, monkeypatch):
    engine, uploads, _database = migration_fixture
    name = "1" * 32 + "_legacy.svg"
    (uploads / name).write_bytes(b"synthetic interrupted migration attachment")
    set_reference(engine, "resource_path", name)
    original_copy = migrate._copy_retaining_source

    def interrupted(root, group):
        original_copy(root, group)
        raise RuntimeError("synthetic power loss after exclusive publish")

    monkeypatch.setattr(migrate, "_copy_retaining_source", interrupted)
    report = migrate.inspect_migration(engine, uploads)
    with pytest.raises(RuntimeError, match="synthetic power loss"):
        migrate.apply_migration(engine, uploads, expected_revision=report["revision"], backup_confirmed=True, maintenance_confirmed=True)
    assert (uploads / report["assets"][0]["target"]).exists()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT resource_path FROM toolkit")).scalar_one() == name
        assert connection.execute(text("SELECT status FROM file_operation")).scalar_one() == "pending"
        assert migrate.financial_snapshot(connection) == report["financial_snapshot"]
    monkeypatch.setattr(migrate, "_copy_retaining_source", original_copy)
    fresh = review_and_apply(engine, uploads)
    assert fresh["assets"][0]["decision"] == "copy_and_register"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM file_operation")).scalar_one() == "committed"
        assert connection.execute(text("SELECT COUNT(*) FROM file_reference")).scalar_one() == 1
    review_and_apply(engine, uploads)


def test_revisions_confirmation_and_no_default_database(migration_fixture, monkeypatch, capsys):
    engine, uploads, database = migration_fixture
    report = migrate.inspect_migration(engine, uploads)
    with pytest.raises(ValueError, match="backup"):
        migrate.apply_migration(engine, uploads, expected_revision=report["revision"], maintenance_confirmed=True)
    with pytest.raises(ValueError, match="writers"):
        migrate.apply_migration(engine, uploads, expected_revision=report["revision"], backup_confirmed=True)
    with pytest.raises(ValueError, match="existing business"):
        migrate.apply_migration(engine, uploads, expected_revision=report["revision"], initialize_empty=True, maintenance_confirmed=True)
    with engine.begin() as connection:
        connection.execute(text("UPDATE user SET balance=123 WHERE id=303"))
    with pytest.raises(ValueError, match="revision"):
        migrate.apply_migration(engine, uploads, expected_revision=report["revision"], backup_confirmed=True, maintenance_confirmed=True)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URI", raising=False)
    monkeypatch.setenv("UPLOAD_FOLDER", str(uploads))
    assert migrate.main([]) == 2
    assert "no .env/defaults" in capsys.readouterr().err
    monkeypatch.setenv("DATABASE_URL", str(engine.url))
    assert migrate.main(["--apply", "--expected-database", "not-this-database", "--expected-revision", report["revision"]]) == 2
    assert migrate.main([]) == 0
    assert json.loads(capsys.readouterr().out)["database"] == str(database)
    assert "zhihe_schema_migration" not in inspect(engine).get_table_names()


def test_empty_initialization_and_interrupted_ddl_resume(tmp_path, monkeypatch):
    database = tmp_path / "empty.sqlite"
    database.touch()
    engine = create_engine(f"sqlite:///{database}")
    uploads = tmp_path / "new-uploads"
    report = migrate.inspect_migration(engine, uploads)
    assert report["empty_database"]
    original = migrate._apply_schema_step
    calls = []

    def interrupted(engine, step):
        original(engine, step)
        calls.append(step["id"])
        if len(calls) == 2:
            raise RuntimeError("synthetic interrupted DDL")

    monkeypatch.setattr(migrate, "_apply_schema_step", interrupted)
    with pytest.raises(RuntimeError, match="interrupted DDL"):
        migrate.apply_migration(engine, uploads, expected_revision=report["revision"], initialize_empty=True, maintenance_confirmed=True)
    monkeypatch.setattr(migrate, "_apply_schema_step", original)
    fresh = migrate.inspect_migration(engine, uploads)
    assert fresh["empty_database"] and not fresh["blockers"]
    migrate.apply_migration(engine, uploads, expected_revision=fresh["revision"], initialize_empty=True, maintenance_confirmed=True)
    assert set(db.metadata.tables) <= set(inspect(engine).get_table_names())
    assert not uploads.exists(), "Schema initialization must not silently initialize file storage"
    engine.dispose()


@pytest.mark.mysql
def test_real_mysql_legacy_schema_financial_history_indexes_and_replay(tmp_path):
    with IsolatedMySQL() as instance:
        instance.assert_owned()
        engine = create_engine(instance.uri)
        legacy_schema(engine)
        uploads = tmp_path / "mysql-uploads"
        uploads.mkdir()
        name = "2" * 32 + "_paid.svg"
        (uploads / name).write_bytes(b"synthetic MySQL paid SVG")
        set_reference(engine, "resource_path", name)
        report = review_and_apply(engine, uploads)
        assert report["mysql_ddl_implicit_commit"] is True
        inspector = inspect(engine)
        assert str(next(col for col in inspector.get_columns("toolkit") if col["name"] == "description")["type"]) == "LONGTEXT"
        assert str(next(col for col in inspector.get_columns("lesson") if col["name"] == "content")["type"]) == "LONGTEXT"
        index = next(idx for idx in inspector.get_indexes("order") if idx["name"] == "ix_order_buyer_toolkit_status")
        assert index["column_names"] == ["buyer_id", "toolkit_id", "status"] and not index["unique"]
        with engine.connect() as connection:
            assert migrate.financial_snapshot(connection) == report["financial_snapshot"]
            assert connection.execute(text("SELECT type FROM wallet_transaction")).scalar_one() == "refund"
            assert connection.execute(text("SELECT status FROM `order` WHERE id=903")).scalar_one() == "refunded"
            assert connection.execute(text("SELECT event_key FROM wallet_transaction")).scalar_one() is None
        before = tree_snapshot(uploads)
        review_and_apply(engine, uploads)
        assert tree_snapshot(uploads) == before
        engine.dispose()
        instance.assert_owned()


@pytest.mark.mysql
def test_mysql_implicit_ddl_commit_before_journal_and_enum_upgrade_resume(tmp_path, monkeypatch):
    with IsolatedMySQL() as instance:
        engine = create_engine(instance.uri)
        legacy_schema(engine)
        # This separate synthetic fixture predates refund enum support. The
        # previous MySQL test verifies actual historical refund preservation.
        with engine.begin() as connection:
            connection.execute(text("UPDATE `order` SET status='cancelled' WHERE id=903"))
            connection.execute(text("UPDATE wallet_transaction SET type='recharge'"))
            connection.execute(text("ALTER TABLE `order` MODIFY status ENUM('pending','paid','cancelled') NULL"))
            connection.execute(text("ALTER TABLE wallet_transaction MODIFY type ENUM('recharge','purchase','commission','withdrawal') NOT NULL"))
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        original_journal = migrate._journal_step

        def interrupted(connection, step):
            if step == "column:user:token_version":
                raise RuntimeError("synthetic crash after implicitly committed MySQL DDL")
            original_journal(connection, step)

        report = migrate.inspect_migration(engine, uploads)
        assert {step["action"] for step in report["schema_steps"]} >= {"enum", "longtext", "add_column"}
        monkeypatch.setattr(migrate, "_journal_step", interrupted)
        with pytest.raises(RuntimeError, match="implicitly committed"):
            migrate.apply_migration(engine, uploads, expected_revision=report["revision"], backup_confirmed=True, maintenance_confirmed=True)
        assert "token_version" in {col["name"] for col in inspect(engine).get_columns("user")}
        with engine.connect() as connection:
            assert not connection.execute(text("SELECT step FROM zhihe_schema_migration WHERE step='column:user:token_version'")).first()
            assert migrate.financial_snapshot(connection) == report["financial_snapshot"]
        monkeypatch.setattr(migrate, "_journal_step", original_journal)
        fresh = review_and_apply(engine, uploads)
        assert "column:user:token_version" not in {step["id"] for step in fresh["schema_steps"]}
        inspector = inspect(engine)
        wallet_type = next(col["type"] for col in inspector.get_columns("wallet_transaction") if col["name"] == "type")
        order_status = next(col["type"] for col in inspector.get_columns("order") if col["name"] == "status")
        assert "refund" in wallet_type.enums and "refunded" in order_status.enums
        with engine.connect() as connection:
            assert migrate.financial_snapshot(connection) == report["financial_snapshot"]
        # Existing token values must not be rewritten when repairing only the
        # default for new users after an earlier interrupted/manual rollout.
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE `user` ALTER COLUMN token_version DROP DEFAULT"))
            connection.execute(text("UPDATE user SET token_version=7 WHERE id=101"))
        default_report = review_and_apply(engine, uploads)
        assert "token_default" in {step["action"] for step in default_report["schema_steps"]}
        with engine.connect() as connection:
            assert connection.execute(text("SELECT token_version FROM user WHERE id=101")).scalar_one() == 7
        review_and_apply(engine, uploads)
        instance.assert_owned()
        engine.dispose()
