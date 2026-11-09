"""Private S3 behavior with in-memory SQLite and a fake S3 client only."""
import base64
import hashlib
import io
import re
import sys
from types import SimpleNamespace
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image

import storage
from config import build_config
from extensions import db
from file_service import UploadContext, bind_reference, directory, open_asset, reclaim_asset, recover_file_operations, send_asset
from models import FileAsset, FileOperation, FileReference, Lesson, Toolkit, User, utcnow
from security import APIError, Identity
from test_support import isolated_config


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.fail_head = False
        self.fail_delete = False
        self.corrupt_after_put = False
        self.get_calls = 0
        self.upload_options = []

    def put_object(self, **kwargs):
        body = kwargs["Body"].read()
        assert kwargs["ContentLength"] == len(body)
        if "ChecksumSHA256" in kwargs:
            assert kwargs["ChecksumSHA256"] == base64.b64encode(hashlib.sha256(body).digest()).decode()
        assert "ACL" not in kwargs  # Bucket remains private; no public ACL or URL.
        self.upload_options.append(kwargs.copy())
        self.objects[kwargs["Key"]] = (b"X" * len(body) if self.corrupt_after_put else body,
                                       kwargs["Metadata"].copy())
        return {}

    def head_object(self, **kwargs):
        if self.fail_head:
            raise IOError("Head unavailable after put")
        body, metadata = self.objects[kwargs["Key"]]
        return {"ContentLength": len(body), "Metadata": metadata.copy()}

    def get_object(self, **kwargs):
        self.get_calls += 1
        body, metadata = self.objects[kwargs["Key"]]
        return {"Body": io.BytesIO(body), "ContentLength": len(body), "Metadata": metadata.copy()}

    def delete_object(self, **kwargs):
        if self.fail_delete:
            raise IOError("Delete unavailable")
        self.objects.pop(kwargs["Key"], None)
        return {}


@pytest.fixture
def s3_env(sqlite_app, monkeypatch):
    app = sqlite_app  # Fixture owns an isolated in-memory SQLite database.
    app.config.update(FILE_STORAGE_BACKEND="s3", S3_BUCKET="private-test-bucket",
                      S3_REGION="us-east-1", S3_ENDPOINT_URL="http://127.0.0.1:8333")
    fake = FakeS3()
    monkeypatch.setattr(storage, "_client", lambda: fake)
    with app.app_context():
        user = User(username="owner", nickname="owner", password_hash="unused", token_version=0)
        db.session.add(user)
        db.session.commit()
        identity = Identity(user.id, user.token_version, "a" * 32, utcnow() + timedelta(hours=1))
        yield app, fake, identity


def test_private_object_upload_read_and_attachment(s3_env):
    app, fake, identity = s3_env
    body = b"strictly private resource"
    prepared = UploadContext(identity).prepare("resource", "report.html", io.BytesIO(body), len(body))
    asset = db.session.get(FileAsset, prepared["id"])
    assert asset.storage_backend == "s3"
    assert re.fullmatch(r"files/[0-9a-f]{32}", asset.object_key)
    assert asset.stored_name not in asset.object_key and list(fake.objects) == [asset.object_key]
    assert "ChecksumSHA256" not in fake.upload_options[0] and fake.get_calls == 1
    # Compatible S3 endpoints are read back and hash checked before ready.
    assert asset.size == len(body) and asset.sha256 == hashlib.sha256(body).hexdigest()
    root = Path(app.config["UPLOAD_FOLDER"])
    assert not (root / "resource" / asset.stored_name).exists()
    assert not list((root / ".staging").iterdir())
    with open_asset(asset) as stream:
        assert stream.seekable() and stream.read() == body
        stream.seek(0)
        assert stream.read(8) == body[:8]
    response = send_asset(asset, download_name="报告.html")
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.get_data() == body
    response.close()


def test_upload_rejects_invalid_image_before_s3_and_reclaims_quota(s3_env):
    _, fake, identity = s3_env
    with pytest.raises(ValueError):
        UploadContext(identity).prepare("image", "bad.png", io.BytesIO(b"not-a-png"), 9)
    assert not fake.objects
    assert FileAsset.query.one().state == "deleted"
    assert FileOperation.query.filter_by(action="upload").one().status == "rolled_back"


def test_head_failure_after_put_deletes_abandoned_object(s3_env):
    app, fake, identity = s3_env
    fake.fail_head = True
    with pytest.raises(IOError, match="Head unavailable"):
        UploadContext(identity).prepare("resource", "sample.txt", io.BytesIO(b"body"), 4)
    assert fake.objects == {}
    assert FileAsset.query.one().state == "deleted"
    assert not list((Path(app.config["UPLOAD_FOLDER"]) / ".staging").iterdir())


def test_read_back_detects_s3_endpoint_corruption_before_ready(s3_env):
    _, fake, identity = s3_env
    fake.corrupt_after_put = True
    with pytest.raises(IOError, match="contents do not match"):
        UploadContext(identity).prepare("resource", "sample.txt", io.BytesIO(b"real"), 4)
    assert fake.get_calls == 1 and fake.objects == {}
    assert FileAsset.query.one().state == "deleted"


def test_aws_endpoint_uses_server_verified_checksum_header(s3_env):
    app, fake, identity = s3_env
    app.config["S3_ENDPOINT_URL"] = None
    UploadContext(identity).prepare("resource", "sample.txt", io.BytesIO(b"body"), 4)
    assert fake.upload_options[0]["ChecksumSHA256"] == base64.b64encode(hashlib.sha256(b"body").digest()).decode()
    assert fake.get_calls == 0  # The S3 service verifies this header itself.


def test_reclaim_retry_and_idempotent_missing_object(s3_env):
    _, fake, identity = s3_env
    prepared = UploadContext(identity).prepare("resource", "sample.txt", io.BytesIO(b"payload"), 7)
    fake.fail_delete = True
    assert not reclaim_asset(prepared["id"], failed_upload=True)
    row = db.session.get(FileAsset, prepared["id"])
    assert row.state == "retained" and row.object_key in fake.objects
    fake.fail_delete = False
    row.created_at = utcnow() - timedelta(days=2)
    db.session.commit()
    assert recover_file_operations() == {"reclaimed": 1, "retained": 0}
    assert db.session.get(FileAsset, row.id).state == "deleted"
    assert not fake.objects
    assert not reclaim_asset(row.id)  # Reclaim is safe to repeat.


def test_s3_mapping_never_falls_back_to_same_name_local_file(s3_env):
    _, fake, identity = s3_env
    prepared = UploadContext(identity).prepare("resource", "sample.txt", io.BytesIO(b"remote"), 6)
    asset = db.session.get(FileAsset, prepared["id"])
    root = directory("resource")
    root.mkdir(parents=True)
    (root / asset.stored_name).write_bytes(b"wrong local content")
    fake.objects.pop(asset.object_key)
    with pytest.raises(KeyError), open_asset(asset):
        pass
    with pytest.raises(KeyError):
        send_asset(asset)  # S3 outage is not a local 404 or a local-file fallback.
    fake.objects[asset.object_key] = (b"badbad", {"sha256": asset.sha256})
    with pytest.raises(IOError, match="contents"), open_asset(asset):
        pass
    fake.objects[asset.object_key] = (b"remote", {"sha256": "0" * 64})
    with pytest.raises(IOError, match="metadata"), open_asset(asset):
        pass
    asset.storage_backend = "local"
    with pytest.raises(ValueError, match="mapping"), open_asset(asset):
        pass
    db.session.rollback()


def test_lesson_raw_purpose_and_reference_guard(s3_env):
    _, fake, identity = s3_env
    raw = b"<html>private lesson source</html>"
    prepared = UploadContext(identity).prepare("lesson_raw", "lesson.html", io.BytesIO(raw), len(raw))
    asset = db.session.get(FileAsset, prepared["id"])
    assert asset.object_key in fake.objects
    with pytest.raises(ValueError, match="文档文件名无效"):
        UploadContext(identity).prepare("lesson_raw", "unsafe.exe", io.BytesIO(raw), len(raw))
    toolkit = Toolkit(title="test", slug="test", summary="summary", description="body",
                      category="生信分析", author_id=identity.uid)
    db.session.add(toolkit)
    db.session.flush()
    lesson = Lesson(toolkit_id=toolkit.id, title="lesson", content="text")
    db.session.add(lesson)
    db.session.flush()
    bind_reference(db.session, asset, identity, toolkit.id, "lesson_raw", lesson.id)
    db.session.commit()
    assert FileReference.query.filter_by(asset_id=asset.id, usage="lesson_raw").one().binding_key == f"lesson:{lesson.id}:raw"
    assert not reclaim_asset(asset.id)
    response = send_asset(asset)
    assert response.headers["Content-Disposition"].startswith("attachment;")
    response.close()


def test_inline_preview_requires_validated_image_or_pdf(s3_env):
    _, _, identity = s3_env
    image = io.BytesIO()
    Image.new("RGB", (3, 2), color="red").save(image, format="PNG")
    png = image.getvalue()
    image_asset_id = UploadContext(identity).prepare("resource", "preview.png", io.BytesIO(png), len(png))["id"]
    image_asset = db.session.get(FileAsset, image_asset_id)
    attachment = send_asset(image_asset)
    assert attachment.headers["Content-Disposition"].startswith("attachment;")
    attachment.close()
    preview = send_asset(image_asset, inline_preview=True)
    assert preview.headers["Content-Disposition"].startswith("inline;")
    assert preview.headers["Content-Security-Policy"] == "sandbox"
    assert preview.headers["X-Content-Type-Options"] == "nosniff"
    assert preview.get_data() == png
    preview.close()
    pdf = b"%PDF-1.7\nvalid-looking document"
    pdf_id = UploadContext(identity).prepare("resource", "preview.pdf", io.BytesIO(pdf), len(pdf))["id"]
    pdf_preview = send_asset(db.session.get(FileAsset, pdf_id), inline_preview=True)
    assert pdf_preview.mimetype == "application/pdf" and pdf_preview.get_data() == pdf
    pdf_preview.close()
    html_id = UploadContext(identity).prepare("resource", "danger.html", io.BytesIO(b"<script>"), 8)["id"]
    with pytest.raises(ValueError, match="safe resource MIME"):
        send_asset(db.session.get(FileAsset, html_id), inline_preview=True)
    invalid_id = UploadContext(identity).prepare("resource", "spoofed.png", io.BytesIO(b"<script>"), 8)["id"]
    with pytest.raises(ValueError, match="image preview"):
        send_asset(db.session.get(FileAsset, invalid_id), inline_preview=True)
    raw_id = UploadContext(identity).prepare("lesson_raw", "raw.pdf", io.BytesIO(pdf), len(pdf))["id"]
    with pytest.raises(ValueError, match="safe resource MIME"):
        send_asset(db.session.get(FileAsset, raw_id), inline_preview=True)


def test_quota_reservations_and_recovery_use_persisted_backend(s3_env):
    app, fake, identity = s3_env
    app.config["FILE_USER_QUOTA"] = 4
    first = UploadContext(identity).prepare("resource", "first.txt", io.BytesIO(b"four"), 4)
    with pytest.raises(APIError) as denied:
        UploadContext(identity).prepare("resource", "second.txt", io.BytesIO(b"x"), 1)
    assert denied.value.code == "FILE_QUOTA_EXCEEDED"
    assert reclaim_asset(first["id"], failed_upload=True)
    assert not fake.objects
    second = UploadContext(identity).prepare("resource", "second.txt", io.BytesIO(b"x"), 1)
    assert db.session.get(FileAsset, second["id"]).state == "ready"


def test_historical_local_assets_remain_local_when_s3_is_default(s3_env):
    app, fake, identity = s3_env
    stored = "b" * 32 + "_old.txt"
    local_dir = directory("resource")
    local_dir.mkdir(parents=True)
    (local_dir / stored).write_bytes(b"old")
    asset = FileAsset(stored_name=stored, owner_id=identity.uid, purpose="resource",
                      size=3, state="ready", storage_backend=None, object_key=None)
    db.session.add(asset)
    db.session.commit()
    with open_asset(asset) as handle:
        assert handle.read() == b"old"
    (local_dir / stored).unlink()
    with pytest.raises(APIError) as missing:
        send_asset(asset)
    assert missing.value.status == 404
    (local_dir / stored).write_bytes(b"old")
    assert reclaim_asset(asset.id, failed_upload=True)
    assert not (local_dir / stored).exists() and not fake.objects


def test_legacy_lesson_raw_path_protects_new_local_owned_asset(s3_env):
    app, fake, identity = s3_env
    app.config["FILE_STORAGE_BACKEND"] = "local"
    raw = UploadContext(identity).prepare("lesson_raw", "source.md", io.BytesIO(b"lesson"), 6)
    asset = db.session.get(FileAsset, raw["id"])
    path = directory("lesson_raw") / asset.stored_name
    assert asset.storage_backend == "local" and path.read_bytes() == b"lesson"
    toolkit = Toolkit(title="local", slug="local", summary="summary", description="body",
                      category="生信分析", author_id=identity.uid)
    db.session.add(toolkit)
    db.session.flush()
    lesson = Lesson(toolkit_id=toolkit.id, title="raw", content="body", raw_file_path=str(path))
    db.session.add(lesson)
    db.session.commit()
    app.config["FILE_STORAGE_BACKEND"] = "s3"
    assert not reclaim_asset(asset.id, failed_upload=True) and path.exists()
    lesson.raw_file_path = None
    db.session.commit()
    assert reclaim_asset(asset.id, failed_upload=True) and not path.exists()
    assert fake.objects == {}



def test_migrated_lesson_raw_read_uses_request_limit_not_other_asset_limit(s3_env):
    app, fake, identity = s3_env
    app.config.update(FILE_MAX_SIZE=8, MAX_CONTENT_LENGTH=24)

    def registered(purpose, content, suffix):
        digest = hashlib.sha256(content).hexdigest()
        key = f"files/{len(fake.objects) + 1:032x}"
        asset = FileAsset(stored_name=f"{len(fake.objects) + 1:032x}_{purpose}.{suffix}",
                          owner_id=identity.uid, purpose=purpose, size=len(content),
                          sha256=digest, state="legacy", storage_backend="s3", object_key=key)
        db.session.add(asset)
        db.session.commit()
        fake.objects[key] = (content, {"sha256": digest})
        return asset

    raw = registered("lesson_raw", b"r" * 9, "pdf")
    with open_asset(raw) as stream:
        assert stream.read() == b"r" * 9
    response = send_asset(raw)
    assert response.get_data() == b"r" * 9
    response.close()
    at_limit = registered("lesson_raw", b"x" * 24, "pdf")
    with open_asset(at_limit) as stream:
        assert stream.read() == b"x" * 24
    get_calls = fake.get_calls
    too_large = registered("lesson_raw", b"x" * 25, "pdf")
    with pytest.raises(IOError, match="metadata"):
        with open_asset(too_large):
            pass
    assert fake.get_calls == get_calls
    for purpose, suffix in (("resource", "txt"), ("image", "png"), ("asset", "png")):
        oversized = registered(purpose, b"y" * 9, suffix)
        with pytest.raises(IOError, match="metadata"):
            with open_asset(oversized):
                pass
    assert fake.get_calls == get_calls


def test_historical_legacy_rows_remain_unreclaimable(s3_env):
    _, fake, identity = s3_env
    local = directory("lesson_raw")
    local.mkdir(parents=True, exist_ok=True)
    old_name = "a" * 32 + "_old.pdf"
    original = local / old_name
    original.write_bytes(b"historical original")
    historical = FileAsset(stored_name=old_name, owner_id=identity.uid, purpose="lesson_raw",
                           size=original.stat().st_size, state="legacy", storage_backend="local")
    key = "files/" + "b" * 32
    orphan = FileAsset(stored_name="b" * 32 + "_unowned.pdf", owner_id=None,
                       purpose="lesson_raw", size=3, sha256=hashlib.sha256(b"old").hexdigest(),
                       state="legacy", storage_backend="s3", object_key=key)
    db.session.add_all([historical, orphan])
    db.session.commit()
    fake.objects[key] = (b"old", {"sha256": orphan.sha256})
    assert not reclaim_asset(historical.id)
    assert not reclaim_asset(orphan.id)
    assert historical.state == orphan.state == "legacy"
    assert original.read_bytes() == b"historical original" and key in fake.objects

def test_s3_client_created_only_at_first_use(sqlite_app, monkeypatch):
    calls = []
    client = object()
    fake_config = SimpleNamespace(Config=lambda **kwargs: kwargs)
    monkeypatch.setitem(sys.modules, "botocore", SimpleNamespace(config=fake_config))
    monkeypatch.setitem(sys.modules, "botocore.config", fake_config)
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda service, **kwargs:
                        calls.append((service, kwargs)) or client))
    with sqlite_app.app_context():
        sqlite_app.config.update(S3_REGION="us-east-1", S3_ENDPOINT_URL="http://127.0.0.1:8333")
        assert "zhihe.s3_client" not in sqlite_app.extensions
        assert storage._client() is client and storage._client() is client
        assert len(calls) == 1 and calls[0][0] == "s3"
        assert calls[0][1]["endpoint_url"] == "http://127.0.0.1:8333"
        assert calls[0][1]["config"] == {
            "s3": {"addressing_style": "path"},
            "retries": {"mode": "standard", "max_attempts": 3},
            "connect_timeout": 5, "read_timeout": 30,
        }


def test_s3_config_requires_private_endpoint_and_explicit_bucket(tmp_path):
    values = isolated_config("sqlite:///:memory:", tmp_path / "uploads")
    values.update(FILE_STORAGE_BACKEND="s3", S3_BUCKET="test-private-bucket", S3_REGION="us-east-1")
    assert build_config(values)["FILE_STORAGE_BACKEND"] == "s3"
    values["S3_ENDPOINT_URL"] = "http://127.0.0.1:8333"
    assert build_config(values)["S3_ENDPOINT_URL"] == values["S3_ENDPOINT_URL"]
    values["S3_ENDPOINT_URL"] = "http://remote.example:8333"
    with pytest.raises(ValueError, match="HTTPS"):
        build_config(values)
    values["S3_ENDPOINT_URL"] = "https://s3.example"
    values["S3_BUCKET"] = None
    with pytest.raises(ValueError, match="S3_BUCKET"):
        build_config(values)
