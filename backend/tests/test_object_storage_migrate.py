"""Disposable in-memory SQLite and temporary paths only; never contact production."""
import hashlib
import io
from pathlib import Path
import re
import uuid

import pytest
from sqlalchemy import inspect

import object_storage_migrate as migration
import storage
from extensions import db
from file_service import reclaim_asset
from models import FileAsset, FileReference, Lesson, Toolkit, User


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.uploads = 0
        self.corrupt = False

    def put_object(self, **kwargs):
        body = kwargs["Body"].read()
        assert len(body) == kwargs["ContentLength"]
        assert "ACL" not in kwargs
        self.uploads += 1
        self.objects[kwargs["Key"]] = (body, kwargs["Metadata"].copy())
        return {}

    def head_object(self, **kwargs):
        body, metadata = self.objects[kwargs["Key"]]
        return {"ContentLength": len(body), "Metadata": metadata}

    def get_object(self, **kwargs):
        body, metadata = self.objects[kwargs["Key"]]
        if self.corrupt:
            body = b"!" * len(body)
        return {"Body": io.BytesIO(body), "ContentLength": len(body), "Metadata": metadata}

    def delete_object(self, **kwargs):
        self.objects.pop(kwargs["Key"], None)
        return {}


@pytest.fixture
def files(sqlite_app, monkeypatch):
    """A fully isolated app with one registered asset and one historical raw."""
    app = sqlite_app
    app.config.update(S3_BUCKET="private-test-bucket", S3_REGION="us-east-1",
                      S3_ENDPOINT_URL="http://127.0.0.1:8333")
    fake = FakeS3()
    monkeypatch.setattr(storage, "_client", lambda: fake)
    root = Path(app.config["UPLOAD_FOLDER"])
    (root / "resource").mkdir(parents=True)
    (root / "lesson_raw").mkdir(parents=True)
    with app.app_context():
        author = User(username="migration-author", nickname="author", password_hash="unused")
        db.session.add(author)
        db.session.flush()
        kit = Toolkit(title="Fixture", slug="fixture-migration", summary="fixture",
                      description="fixture", category="bioinformatics", author_id=author.id)
        db.session.add(kit)
        db.session.flush()
        lesson = Lesson(toolkit_id=kit.id, title="Lesson", content="fixture")
        db.session.add(lesson)
        db.session.flush()
        raw = root / "lesson_raw" / f"lesson_{lesson.id}_old_document.pdf"
        raw.write_bytes(b"legacy original document")
        lesson.raw_file_path = str(raw)
        name = f"{uuid.uuid4().hex}_registered.txt"
        local = root / "resource" / name
        local.write_bytes(b"registered local resource")
        asset = FileAsset(stored_name=name, owner_id=author.id, purpose="resource", size=local.stat().st_size,
                          state="active", is_public=False)
        db.session.add(asset)
        db.session.commit()
        asset_id, lesson_id, author_id = asset.id, lesson.id, author.id
        yield app, fake, local, raw, asset_id, lesson_id, author_id


def review(app):
    return migration.inspect_migration(app, allow_test_sqlite=True)


def apply(app, report, **kwargs):
    return migration.apply_migration(app, database_name="sqlite-test",
                                    expected_revision=report["revision"], backup_confirmed=True,
                                    maintenance_confirmed=True, allow_test_sqlite=True, **kwargs)


def test_dry_run_and_resumable_copy_preserves_both_originals(files):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    with app.app_context():
        before = review(app)
        assert re.fullmatch("[0-9a-f]{64}", before["revision"])
        assert before["anomalies"] == []
        assert before["counts"]["local_pending"] == before["counts"]["legacy_raw_pending"] == 1
        assert fake.uploads == 0 and not fake.objects
        assert FileReference.query.count() == 0
        with pytest.raises(migration.MigrationSafetyError, match="Apply requires"):
            migration.apply_migration(app, database_name="sqlite-test", expected_revision=before["revision"],
                                      allow_test_sqlite=True)
        with pytest.raises(migration.MigrationSafetyError, match="Database identity"):
            migration.apply_migration(app, database_name="production", expected_revision=before["revision"],
                                      backup_confirmed=True, maintenance_confirmed=True, allow_test_sqlite=True)
        with pytest.raises(migration.MigrationSafetyError, match="revision"):
            migration.apply_migration(app, database_name="sqlite-test", expected_revision="0" * 64,
                                      backup_confirmed=True, maintenance_confirmed=True, allow_test_sqlite=True)
        assert fake.uploads == 0
        result = apply(app, before)
        assert result["status"] == "applied"
        assert result["counts"]["assets_copied"] == result["counts"]["raw_registered"] == 1
        assert local.read_bytes() == b"registered local resource"
        assert raw.read_bytes() == b"legacy original document"
        db.session.expire_all()
        asset = db.session.get(FileAsset, asset_id)
        assert asset.storage_backend == "s3"
        assert asset.sha256 == hashlib.sha256(local.read_bytes()).hexdigest()
        assert re.fullmatch(r"files/[0-9a-f]{32}", asset.object_key)
        assert asset.stored_name not in asset.object_key
        ref = FileReference.query.filter_by(lesson_id=lesson_id, usage="lesson_raw").one()
        assert ref.binding_key == f"lesson:{lesson_id}:raw"
        assert ref.toolkit_id == db.session.get(Lesson, lesson_id).toolkit_id
        assert ref.asset.owner_id == author_id and ref.asset.state == "active"
        assert ref.asset.purpose == "lesson_raw" and ref.asset.is_public is False
        assert ref.asset.sha256 == hashlib.sha256(raw.read_bytes()).hexdigest()
        assert ref.asset.stored_name != raw.name
        assert db.session.get(Lesson, lesson_id).raw_file_path == str(raw)
        assert fake.uploads == len(fake.objects) == 2
        for key, (body, metadata) in fake.objects.items():
            assert re.fullmatch(r"files/[0-9a-f]{32}", key)
            assert metadata["sha256"] == hashlib.sha256(body).hexdigest()
        again = review(app)
        assert again["counts"]["local_pending"] == again["counts"]["legacy_raw_pending"] == 0
        assert again["counts"]["s3_already"] == 2
        rerun = apply(app, again)
        assert rerun["counts"]["assets_copied"] == rerun["counts"]["raw_registered"] == 0
        assert fake.uploads == 2 and FileReference.query.count() == 1



@pytest.mark.parametrize("state", ["active", "legacy"])
def test_migrated_raw_reclaim_deletes_only_owned_s3_copy(files, state):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    with app.app_context():
        result = apply(app, review(app))
        assert result["counts"]["raw_registered"] == 1
        ref = FileReference.query.filter_by(lesson_id=lesson_id, usage="lesson_raw").one()
        migrated = ref.asset
        assert migrated.state == "active"
        assert not reclaim_asset(migrated.id)  # A live reference protects the S3 copy.
        if state == "legacy":
            migrated.state = "legacy"  # Rows written by earlier migration code.
            db.session.commit()
        key, migrated_id = migrated.object_key, migrated.id
        db.session.delete(ref)
        db.session.commit()
        assert reclaim_asset(migrated_id)
        db.session.expire_all()
        assert db.session.get(FileAsset, migrated_id).state == "deleted"
        assert key not in fake.objects and len(fake.objects) == 1
        assert raw.read_bytes() == b"legacy original document"
        assert db.session.get(Lesson, lesson_id).raw_file_path == str(raw)
        assert local.read_bytes() == b"registered local resource"
        assert not reclaim_asset(migrated_id)

def test_only_four_missing_nullable_columns_are_added(files):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    with app.app_context():
        db.session.remove()
        with db.engine.begin() as connection:
            for table, column in (("file_asset", "object_key"), ("file_asset", "storage_backend"),
                                  ("batch_job", "package_sha256"), ("batch_job", "package_object_key")):
                connection.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
        report = review(app)
        assert len(report["schema_columns_to_add"]) == 4
        assert fake.uploads == 0
        columns = {t: {x["name"] for x in inspect(db.engine).get_columns(t)}
                   for t in ("file_asset", "batch_job")}
        assert "object_key" not in columns["file_asset"]
        result = apply(app, report)
        assert result["schema_columns_added"] == report["schema_columns_to_add"]
        for table, names in migration.COLUMNS.items():
            actual = {x["name"]: x for x in inspect(db.engine).get_columns(table)}
            for name in names:
                assert actual[name]["nullable"]
        assert local.exists() and raw.exists()
        assert review(app)["schema_columns_to_add"] == []


def test_failed_s3_readback_keeps_local_mapping_then_retry(files):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    with app.app_context():
        fake.corrupt = True
        report = review(app)
        result = apply(app, report)
        assert result["status"] == "partial" and result["counts"]["failed"] == 2
        db.session.expire_all()
        row = db.session.get(FileAsset, asset_id)
        assert row.storage_backend is None and row.object_key is None
        assert FileReference.query.count() == 0
        assert local.exists() and raw.exists()
        # Unknown private bytes are never deleted; next pass uses fresh UUID keys.
        assert fake.uploads == 2 and len(fake.objects) == 2
        fake.corrupt = False
        second = apply(app, review(app))
        assert second["counts"]["failed"] == 0
        assert second["counts"]["assets_copied"] == second["counts"]["raw_registered"] == 1
        assert local.exists() and raw.exists() and len(fake.objects) == 4


@pytest.mark.parametrize("bad", ["symlink", "escape", "changed_size", "hardlink"])
def test_anomalous_registered_file_blocks_all_writes(files, tmp_path, bad):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    with app.app_context():
        if bad == "symlink":
            original = tmp_path / "original"
            local.rename(original)
            local.symlink_to(original)
        elif bad == "escape":
            db.session.get(FileAsset, asset_id).stored_name = "../outside"
            db.session.commit()
        elif bad == "hardlink":
            (tmp_path / "second-link").hardlink_to(local)
        else:
            local.write_bytes(b"modified after registration")
        report = review(app)
        expected = f"invalid_asset_metadata:{asset_id}" if bad == "escape" else f"invalid_local_file:{asset_id}"
        assert report["anomalies"] == [expected]
        with pytest.raises(migration.MigrationSafetyError, match="anomalies"):
            apply(app, report)
        assert fake.uploads == 0 and FileReference.query.count() == 0
        assert raw.exists()


@pytest.mark.parametrize("bad", ["outside", "wrong_lesson", "symlink", "relative"])
def test_anomalous_legacy_raw_blocks_all_writes(files, tmp_path, bad):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    with app.app_context():
        if bad == "outside":
            target = tmp_path / f"lesson_{lesson_id}_elsewhere.pdf"
            target.write_bytes(b"outside")
            db.session.get(Lesson, lesson_id).raw_file_path = str(target)
        elif bad == "wrong_lesson":
            db.session.get(Lesson, lesson_id).raw_file_path = str(raw).replace(
                f"lesson_{lesson_id}_", "lesson_999_")
        elif bad == "symlink":
            target = tmp_path / "raw-original"
            raw.rename(target)
            raw.symlink_to(target)
        else:
            db.session.get(Lesson, lesson_id).raw_file_path = "../lesson_raw/not-safe.pdf"
        db.session.commit()
        report = review(app)
        assert report["anomalies"] == [f"invalid_legacy_raw:{lesson_id}"]
        with pytest.raises(migration.MigrationSafetyError):
            apply(app, report)
        assert fake.uploads == 0 and local.exists()


def test_revision_becomes_stale_when_file_changes_without_changing_size(files):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    with app.app_context():
        approved = review(app)
        local.write_bytes(b"x" * local.stat().st_size)
        with pytest.raises(migration.MigrationSafetyError, match="revision"):
            apply(app, approved)
        assert fake.uploads == 0


def test_sqlite_test_opt_in_is_not_exposed_as_mysql_default(files):
    app, *_ = files
    with app.app_context():
        with pytest.raises(migration.MigrationSafetyError, match="verified MySQL"):
            migration.inspect_migration(app)
        app.config["TESTING"] = False
        with pytest.raises(migration.MigrationSafetyError, match="restricted"):
            migration.inspect_migration(app, allow_test_sqlite=True)


def test_existing_linked_original_symlink_is_not_silently_ignored(files, tmp_path):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    with app.app_context():
        apply(app, review(app))
        original = tmp_path / "original-raw"
        raw.rename(original)
        raw.symlink_to(original)
        report = review(app)
        assert report["anomalies"] == [f"invalid_legacy_raw:{lesson_id}"]
        with pytest.raises(migration.MigrationSafetyError, match="anomalies"):
            apply(app, report)
        assert fake.uploads == 2


def test_unknown_orphan_bytes_are_never_inventory_deleted(files):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    unrelated = local.parent / f"{uuid.uuid4().hex}_orphan.txt"
    unrelated.write_bytes(b"not registered; do not touch")
    with app.app_context():
        report = review(app)
        assert report["anomalies"] == []
        apply(app, report)
        assert unrelated.read_bytes() == b"not registered; do not touch"
        apply(app, review(app))
        assert unrelated.read_bytes() == b"not registered; do not touch"


def test_pathname_replacement_during_upload_does_not_switch_asset(files, tmp_path, monkeypatch):
    app, fake, local, raw, asset_id, lesson_id, author_id = files
    actual_put = storage.put_file
    def replace_after_put(key, path, size, digest):
        actual_put(key, path, size, digest)
        if local.exists() and local.is_file():
            local.rename(tmp_path / "reviewed-original")
            local.write_bytes(b"replaced by another writer")
    monkeypatch.setattr(storage, "put_file", replace_after_put)
    with app.app_context():
        result = apply(app, review(app))
        assert result["status"] == "partial"
        assert result["counts"]["assets_copied"] == 0
        db.session.expire_all()
        asset = db.session.get(FileAsset, asset_id)
        assert asset.storage_backend is None and asset.object_key is None
        assert local.exists() and (tmp_path / "reviewed-original").exists()
