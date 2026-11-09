"""Owned files, durable staging operations and shared database references.

A URL is never a deletion capability. Known files are reclaimed only after a
committed reference change and a fresh locked reference check. Legacy files
without registry entries are served conservatively and never scanned/deleted.
"""
import hashlib
import mimetypes
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit
from flask import Response, current_app
from sqlalchemy import select, delete, or_
from sqlalchemy.orm import Session
from PIL import Image, UnidentifiedImageError
from defusedxml import ElementTree as SafeET
from extensions import db
import storage
from models import FileAsset, FileReference, FileOperation, Toolkit, Lesson, User, ASSET_PATH, LESSON_ASSET_PATH
from security import APIError, assert_identity
from money_service import lock_users
from validation import IMAGE_EXTENSIONS, rejected_resource_extension, string_field, image_source

STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}_[A-Za-z0-9._\-]{1,200}$")
KINDS = {"image", "resource", "asset", "lesson_raw"}


def safe_stored_name(stored):
    return isinstance(stored, str) and bool(STORED_NAME_RE.fullmatch(stored))


def directory(kind):
    if kind not in KINDS:
        raise ValueError("Unknown file purpose")
    return Path(current_app.config["UPLOAD_FOLDER"]) / kind


def init_storage():
    root = Path(current_app.config["UPLOAD_FOLDER"])
    kinds = KINDS if current_app.config["FILE_STORAGE_BACKEND"] == "local" else ()
    for kind in [*kinds, ".staging", ".batches"]:
        (root / kind).mkdir(parents=True, exist_ok=True)


def _asset_backend(asset):
    backend = asset.storage_backend or "local"  # Historical rows predate object storage.
    if backend == "s3" and asset.object_key:
        storage._check_key(asset.object_key)
    elif backend == "local" and not asset.object_key:
        pass
    else:
        # Never fall back to a similarly named local file for a mapped object.
        raise ValueError("Invalid asset storage mapping")
    return backend


@contextmanager
def open_asset(asset):
    """Yield a seekable verified binary stream; caller must enforce authorization.

    S3 content is bounded, checked against SHA-256 and downloaded into an
    automatically unlinked temporary file (never kept in process memory).
    """
    if asset.state not in {"ready", "active", "retained", "legacy"}:
        raise FileNotFoundError("Asset is not available")
    backend = _asset_backend(asset)
    if backend == "s3":
        init_storage()
        with tempfile.TemporaryFile(mode="w+b", dir=directory_staging()) as stream:
            # Pre-registry lesson originals can be as large as the request cap.
            # Keep images and other resources at their normal per-file limit.
            maximum = (current_app.config["MAX_CONTENT_LENGTH"] if asset.purpose == "lesson_raw"
                       else current_app.config["FILE_MAX_SIZE"])
            storage.read_to_file(asset.object_key, stream, asset.size, asset.sha256, maximum)
            yield stream
    else:
        if not safe_stored_name(asset.stored_name):
            raise ValueError("Invalid local stored name")
        path = directory(asset.purpose) / asset.stored_name
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Local asset is not a regular file")
            with os.fdopen(fd, "rb") as stream:
                fd = -1
                yield stream
        finally:
            if fd >= 0:
                os.close(fd)


def directory_staging():
    return Path(current_app.config["UPLOAD_FOLDER"]) / ".staging"


_INLINE_PREVIEW_TYPES = {
    "image/png": "PNG", "image/jpeg": "JPEG", "image/gif": "GIF",
    "image/webp": "WEBP", "application/pdf": "PDF",
}


def _validate_inline_preview(stream, mime):
    """Inspect the actual content before allowing a resource to render inline."""
    size = os.fstat(stream.fileno()).st_size
    if size > current_app.config["FILE_MAX_SIZE"]:
        raise ValueError("Preview exceeds the file size limit")
    stream.seek(0)
    if mime == "application/pdf":
        if stream.read(5) != b"%PDF-":
            raise ValueError("Invalid PDF preview")
    else:
        try:
            with Image.open(stream) as image:
                if image.format != _INLINE_PREVIEW_TYPES[mime]:
                    raise ValueError("Preview image format mismatch")
                width, height = image.size
                if (min(width, height) < 1 or
                        max(width, height) > current_app.config["IMAGE_MAX_DIMENSION"] or
                        width * height > current_app.config["IMAGE_MAX_PIXELS"]):
                    raise ValueError("Preview image dimensions exceed limits")
                image.verify()
            stream.seek(0)
            with Image.open(stream) as image:
                pixels = frames = 0
                while True:
                    width, height = image.size
                    frames += 1
                    pixels += width * height
                    if frames > 200 or pixels > current_app.config["IMAGE_MAX_PIXELS"]:
                        raise ValueError("Preview animation exceeds limits")
                    image.load()
                    try:
                        image.seek(frames)
                    except EOFError:
                        break
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
                Image.DecompressionBombWarning) as exc:
            raise ValueError("Invalid image preview") from exc
    stream.seek(0)


def send_asset(asset, download_name=None, as_attachment=False, mimetype=None, inline_preview=False):
    """Return a private, no-store Flask response for an already authorized row."""
    if not safe_stored_name(asset.stored_name):
        raise ValueError("Invalid stored name")
    filename = download_name or asset.stored_name.split("_", 1)[1]
    if (not isinstance(filename, str) or len(filename) > 255 or not filename or
            any(c in filename for c in ("/", "\\", "\r", "\n", "\x00")) or
            any(ord(c) < 32 or ord(c) == 127 for c in filename)):
        raise ValueError("Invalid download name")
    # A display filename may be user controlled; derive default MIME only from
    # the validated immutable stored name, never from Content-Disposition.
    effective_mime = mimetype or mimetypes.guess_type(asset.stored_name)[0] or "application/octet-stream"
    if inline_preview and (asset.purpose != "resource" or as_attachment or
                           effective_mime not in _INLINE_PREVIEW_TYPES):
        raise ValueError("Inline preview requires a safe resource MIME type")
    # Documents and arbitrary resources must never execute as same-origin HTML.
    attachment = as_attachment or (asset.purpose not in {"image", "asset"} and not inline_preview)
    handle = open_asset(asset)
    try:
        stream = handle.__enter__()  # Open and verify before sending response headers.
    except FileNotFoundError as exc:
        if _asset_backend(asset) == "local":
            raise APIError("文件不存在", 404) from exc
        raise  # S3 failure is never interpreted as a missing local file.
    try:
        if inline_preview:
            _validate_inline_preview(stream, effective_mime)
        response = Response(iter(lambda: stream.read(128 * 1024), b""), mimetype=effective_mime)
        response.content_length = os.fstat(stream.fileno()).st_size
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if inline_preview:
            response.headers["Content-Security-Policy"] = "sandbox"
        from werkzeug.utils import secure_filename
        fallback = secure_filename(filename) or "download"
        response.headers["Content-Disposition"] = (
            f'{"attachment" if attachment else "inline"}; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}")
        response.call_on_close(lambda: handle.__exit__(None, None, None))
        return response
    except BaseException:
        import sys
        handle.__exit__(*sys.exc_info())
        raise


def _validate_svg(path):
    # A small, passive subset. Reject entities, external links, embedded
    # HTML/scripts/CSS/animations and active attributes even inside namespaces.
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("SVG 文件不能超过 2MB")
    try:
        root = SafeET.parse(path, forbid_dtd=True, forbid_entities=True, forbid_external=True).getroot()
    except Exception as exc:
        raise ValueError("SVG 含不安全内容或格式无效") from exc
    allowed = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "defs", "lineargradient", "radialgradient", "stop", "clippath", "mask", "title", "desc", "use", "symbol"}
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ValueError("SVG 根元素无效")
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1].lower() if isinstance(elem.tag, str) else ""
        if tag not in allowed:
            raise ValueError("SVG 含活动或不支持的元素")
        for raw, value in elem.attrib.items():
            key = raw.rsplit("}", 1)[-1].lower()
            val = value.strip().lower()
            if key.startswith("on") or key in {"style", "base"}:
                raise ValueError("SVG 含活动属性")
            if key == "href" and not value.startswith("#"):
                raise ValueError("SVG 不允许外部或嵌入内容")
            if any(word in val for word in ["javascript:", "data:", "http:", "https:", "file:", "@import", "expression("]):
                raise ValueError("SVG 不允许外部或活动内容")
            for target in re.findall(r"url\s*\((.*?)\)", val):
                if not target.strip(" \t\"'").startswith("#"):
                    raise ValueError("SVG 不允许外部资源")
    limit = current_app.config["IMAGE_MAX_DIMENSION"]
    dimensions = []
    for key in ("width", "height"):
        value = root.attrib.get(key)
        if value and not value.endswith("%"):
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(?:px)?", value.strip())
            if not match or not 0 < float(match[1]) <= limit:
                raise ValueError("SVG 尺寸无效或过大")
            dimensions.append(float(match[1]))
    if len(dimensions) == 2 and dimensions[0] * dimensions[1] > current_app.config["IMAGE_MAX_PIXELS"]:
        raise ValueError("SVG 尺寸过大")
    viewbox = root.attrib.get("viewBox")
    if viewbox:
        try:
            values = [float(x) for x in re.split(r"[\s,]+", viewbox.strip())]
            import math
            if len(values) != 4 or not all(math.isfinite(x) for x in values) or not all(0 < x <= limit for x in values[2:]):
                raise ValueError()
        except ValueError as exc:
            raise ValueError("SVG viewBox 无效") from exc


def validate_image(path, suffix):
    if suffix not in IMAGE_EXTENSIONS:
        raise ValueError("图片类型不受支持")
    if suffix == ".svg":
        return _validate_svg(path)
    expected = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".gif": "GIF", ".webp": "WEBP"}[suffix]
    try:
        with Image.open(path) as image:
            width, height = image.size
            if image.format != expected:
                raise ValueError("图片实际格式与扩展名不符")
            if min(width, height) < 1 or max(width, height) > current_app.config["IMAGE_MAX_DIMENSION"] or width * height > current_app.config["IMAGE_MAX_PIXELS"]:
                raise ValueError("图片尺寸过大或无效")
            image.verify()
        # Verify alone does not decode JPEG/GIF pixels. Bounded decoding also
        # catches truncated streams and multi-frame decompression amplification.
        with Image.open(path) as image:
            pixels, frames = 0, 0
            while True:
                width, height = image.size
                frames += 1
                pixels += width * height
                if frames > 200 or pixels > current_app.config["IMAGE_MAX_PIXELS"]:
                    raise ValueError("图片动画帧或累计尺寸过大")
                image.load()
                try:
                    image.seek(frames)
                except EOFError:
                    break
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("图片损坏或尺寸过大") from exc


def _filename(name):
    from werkzeug.utils import secure_filename
    name = string_field(name, "文件名", 200, required=True)
    if any(ch in name for ch in ("/", "\\", "\x00")) or name in {".", ".."}:
        raise ValueError("文件名不能包含路径或空字符")
    raw_stem, suffix = os.path.splitext(name)
    if suffix and not re.fullmatch(r"\.[A-Za-z0-9]{1,16}", suffix):
        raise ValueError("文件扩展名无效")
    # Sanitizing the whole Chinese name can turn 封面.png into 'png', losing
    # the validated extension. Sanitize only its stem and retain the suffix.
    stem = secure_filename(raw_stem) or "file"
    return stem[:max(1, 200 - len(suffix))] + suffix.lower()


class UploadContext:
    """Request-scoped preparations; reused committed assets remain untouched."""
    def __init__(self, identity):
        self.identity = identity
        self.cache = {}
        self.new_assets = set()

    def prepare(self, kind, name, source, size=None, cache_key=None):
        cache = (kind, cache_key) if cache_key else None
        if cache and cache in self.cache:
            return self.cache[cache]
        original_name = name
        name = _filename(name)
        suffix = Path(name).suffix.lower()
        if kind in {"image", "asset"} and suffix not in IMAGE_EXTENSIONS:
            raise ValueError("图片类型不受支持")
        if kind == "lesson_raw":
            from document_converter import detect_content_type
            if not detect_content_type(original_name):
                raise ValueError("文档文件名无效")
        if kind == "resource" and rejected_resource_extension(original_name):
            raise ValueError("不支持该类型的资源附件")
        maximum = current_app.config["FILE_MAX_SIZE"]
        reserve = maximum if size is None else size
        if type(reserve) is not int or not 0 <= reserve <= maximum:
            raise ValueError("文件超过单文件大小限制")
        op_id, stored = uuid.uuid4().hex, f"{uuid.uuid4().hex}_{name}"
        backend = current_app.config["FILE_STORAGE_BACKEND"]
        object_key = f"files/{uuid.uuid4().hex}" if backend == "s3" else None
        # Reservation and operation record commit before creating the file.
        with Session(db.engine, expire_on_commit=False) as session, session.begin():
            owner = lock_users(session, [self.identity.uid])[self.identity.uid]
            assert_identity(session, owner, self.identity)
            used = session.execute(select(db.func.coalesce(db.func.sum(FileAsset.size), 0)).where(
                FileAsset.owner_id == owner.id, FileAsset.state != "deleted")).scalar_one()
            if used + reserve > current_app.config["FILE_USER_QUOTA"]:
                raise APIError("文件配额不足", 409, "FILE_QUOTA_EXCEEDED")
            asset = FileAsset(stored_name=stored, owner_id=owner.id, purpose=kind, size=reserve,
                              state="staging", is_public=False,
                              storage_backend=backend, object_key=object_key)
            session.add(asset)
            session.flush()
            session.add(FileOperation(id=op_id, asset_id=asset.id, action="upload", status="staging"))
            asset_id = asset.id
        self.new_assets.add(asset_id)
        staging = Path(current_app.config["UPLOAD_FOLDER"]) / ".staging" / op_id
        try:
            init_storage()
            actual, digest = 0, hashlib.sha256()
            own_stream = isinstance(source, (str, Path))
            stream = open(source, "rb") if own_stream else source
            try:
                with open(staging, "xb") as output:
                    while True:
                        chunk = stream.read(128 * 1024)
                        if not chunk:
                            break
                        actual += len(chunk)
                        if actual > maximum or (size is not None and actual > size):
                            raise ValueError("文件超过大小限制")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                if own_stream:
                    stream.close()
            if size is not None and actual != size:
                raise ValueError("文件读取长度与登记长度不符")
            if kind in {"image", "asset"}:
                validate_image(staging, suffix)
            if backend == "s3":
                storage.put_file(object_key, staging, actual, digest.hexdigest())
                staging.unlink(missing_ok=True)
            else:
                final = directory(kind) / stored
                os.replace(staging, final)
                directory_fd = os.open(final.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            with Session(db.engine) as session, session.begin():
                owner = lock_users(session, [self.identity.uid])[self.identity.uid]
                assert_identity(session, owner, self.identity)
                row = session.execute(select(FileAsset).where(FileAsset.id == asset_id).with_for_update()
                                      .execution_options(populate_existing=True)).scalar_one()
                row.size, row.sha256, row.state = actual, digest.hexdigest(), "ready"
                session.get(FileOperation, op_id).status = "ready"
        except Exception:
            # This is a known pre-business-commit failure. Freshly locked checks
            # still protect against an ambiguous reservation/readiness commit.
            self.resolve_failure([asset_id])
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        result = {"id": asset_id, "stored_name": stored, "purpose": kind, "name": name, "size": actual}
        if cache:
            self.cache[cache] = result
        return result

    def descriptor(self, kind, descriptor):
        if not descriptor:
            return None
        return self.prepare(kind, descriptor["name"], descriptor["path"], descriptor["size"], descriptor["cache_key"])

    def resolve_failure(self, asset_ids=None):
        """New connection verifies unknown commit outcomes. Unverifiable => keep."""
        for asset_id in sorted(asset_ids if asset_ids is not None else self.new_assets):
            try:
                if reclaim_asset(asset_id, failed_upload=True):
                    self.new_assets.discard(asset_id)
                    self.cache = {key: item for key, item in self.cache.items() if item["id"] != asset_id}
            except Exception:
                current_app.logger.warning("File operation outcome unresolved; retained asset %s", asset_id)


def source_asset(session, url, identity, usage, preserve_unknown=False):
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        image_source(url, "图片")
        return None
    prefix = ASSET_PATH if usage == "cover" else LESSON_ASSET_PATH
    if url.startswith("file:") and usage == "lesson":
        stored = url[5:]
    elif url.startswith(prefix):
        stored = url[len(prefix):]
    elif usage == "lesson" and url.startswith(ASSET_PATH):
        # A public cover may be referenced as an image, without ownership.
        stored = url[len(ASSET_PATH):]
    else:
        raise APIError("图片地址无效")
    if not safe_stored_name(stored):
        raise APIError("图片地址无效")
    asset = session.execute(select(FileAsset).where(FileAsset.stored_name == stored)).scalar_one_or_none()
    if not asset:
        # An edit may leave the exact same historical URL untouched. It does
        # not gain a registry binding, public status, ownership or deletion.
        if preserve_unknown:
            return None
        raise APIError("无法确认图片归属，请重新上传", 400, "UNREGISTERED_ASSET")
    return asset.id


def lock_assets(session, asset_ids):
    rows = {}
    for asset_id in sorted(set(asset_ids)):
        rows[asset_id] = session.execute(select(FileAsset).where(FileAsset.id == asset_id).with_for_update()
                                        .execution_options(populate_existing=True)).scalar_one_or_none()
    return rows


def bind_reference(session, asset, identity, toolkit_id, usage, lesson_id=None, preserve=False,
                   name=None, sort_order=0, binding_key=None):
    if not asset or asset.state not in {"ready", "active", "retained", "legacy"}:
        raise APIError("文件不可用", 409)
    owned = asset.owner_id == identity.uid
    if usage == "cover":
        if not preserve and (asset.purpose != "image" or not (owned or asset.is_public)):
            raise APIError("不能将他人的私有文件公开为封面", 403)
        if not preserve:
            asset.is_public = True
    elif usage == "resource":
        if not preserve and (asset.purpose != "resource" or not owned):
            raise APIError("无权绑定此资源附件", 403)
    elif usage == "lesson":
        if not preserve and (asset.purpose not in {"asset", "image"} or not (owned or (asset.purpose == "image" and asset.is_public))):
            raise APIError("无权绑定此教程图片", 403)
    elif usage == "lesson_raw":
        if not lesson_id or asset.purpose != "lesson_raw" or not owned:
            raise APIError("无权绑定此教程原文件", 403)
    else:
        raise APIError("未知文件用途", 400)
    key = binding_key or (f"lesson:{lesson_id}:image" if usage == "lesson" else
                          f"lesson:{lesson_id}:raw" if usage == "lesson_raw" else
                          f"toolkit:{toolkit_id}:{usage}")
    session.add(FileReference(asset_id=asset.id, toolkit_id=toolkit_id, lesson_id=lesson_id,
                              usage=usage, binding_key=key, name=name, sort_order=sort_order))
    if asset.state != "legacy":
        asset.state = "active"
    for operation in session.execute(select(FileOperation).where(FileOperation.asset_id == asset.id, FileOperation.action == "upload")).scalars():
        operation.status = "committed"


def unbind_toolkit(session, toolkit_id, locked_assets, preserved_reference_ids=()):
    statement = delete(FileReference).where(FileReference.toolkit_id == toolkit_id)
    if preserved_reference_ids:
        statement = statement.where(FileReference.id.notin_(preserved_reference_ids))
    session.execute(statement)
    for asset in locked_assets.values():
        session.add(FileOperation(id=uuid.uuid4().hex, asset_id=asset.id, action="reclaim", status="pending"))


def referenced_asset_ids(session, toolkit_id):
    return list(session.execute(select(FileReference.asset_id).where(FileReference.toolkit_id == toolkit_id)).scalars())


def _legacy_references(session, asset):
    # Migration may not yet have registered old references. Current locked
    # reads prevent an RR snapshot from overlooking a committed reference.
    # Do NOT use FOR UPDATE here: collector cannot acquire a Toolkit lock after
    # FileAsset. The asset's registry lock serializes all NEW bindings; these
    # legacy read-only checks are conservative and migration remains offline.
    query = select(Toolkit.id).where(or_(Toolkit.cover == ASSET_PATH + asset.stored_name,
                                        Toolkit.resource_path == asset.stored_name))
    if session.execute(query).first():
        return True
    if session.execute(select(Lesson.id).where(or_(Lesson.image == "file:" + asset.stored_name,
                                                 Lesson.image == ASSET_PATH + asset.stored_name,
                                                 Lesson.image == LESSON_ASSET_PATH + asset.stored_name))).first():
        return True
    if asset.purpose == "lesson_raw" and _asset_backend(asset) == "local":
        # Protect older raw-file records even when they lack FileReference.
        legacy_path = str(directory("lesson_raw") / asset.stored_name)
        return bool(session.execute(select(Lesson.id).where(Lesson.raw_file_path == legacy_path)).first())
    return False


def reclaim_asset(asset_id, failed_upload=False):
    """Fresh, asset-only transaction. Unknown legacy ownership is never deleted."""
    with Session(db.engine) as session, session.begin():
        asset = session.execute(select(FileAsset).where(FileAsset.id == asset_id).with_for_update()
                                .execution_options(populate_existing=True)).scalar_one_or_none()
        if not asset or asset.owner_id is None or asset.state == "deleted":
            return False
        if asset.state == "legacy":
            # Older migration revisions registered owned S3 lesson originals as
            # legacy. Only their verified private object mapping is reclaimable;
            # never unlink a historical local original, even if it has an owner.
            if (asset.purpose != "lesson_raw" or asset.storage_backend != "s3" or
                    not isinstance(asset.sha256, str) or
                    not re.fullmatch(r"[0-9a-f]{64}", asset.sha256)):
                return False
            try:
                storage._check_key(asset.object_key)
            except ValueError:
                return False
        refs = session.execute(select(FileReference.id).where(FileReference.asset_id == asset_id).with_for_update()).first()
        if refs or _legacy_references(session, asset):
            return False
        if failed_upload and asset.state == "active":
            return False
        # Keep the registry lock while deleting: a new binding can't race the
        # check. The DB operation remains retryable if unlink/commit fails.
        op = FileOperation(id=uuid.uuid4().hex, asset_id=asset.id, action="reclaim", status="pending")
        session.add(op)
        session.flush()
        try:
            if _asset_backend(asset) == "s3":
                storage.delete_file(asset.object_key)
            else:
                if not safe_stored_name(asset.stored_name):
                    raise ValueError("Invalid local stored name")
                (directory(asset.purpose) / asset.stored_name).unlink(missing_ok=True)
            # A crashed writer may have left the operation's private staging
            # file instead of a final file. Only known operation IDs are used;
            # never scan the staging directory or unknown legacy uploads.
            operations = session.execute(select(FileOperation).where(FileOperation.asset_id == asset.id,
                                         FileOperation.action == "upload")).scalars().all()
            for upload in operations:
                if re.fullmatch(r"[0-9a-f]{32}", upload.id):
                    (Path(current_app.config["UPLOAD_FOLDER"]) / ".staging" / upload.id).unlink(missing_ok=True)
        except Exception:
            current_app.logger.warning("File reclaim failed; retained asset %s", asset_id)
            asset.state = "retained"
            op.status = "retained"
            return False
        asset.state, op.status = "deleted", "deleted"
        for old in session.execute(select(FileOperation).where(FileOperation.asset_id == asset.id,
                                    FileOperation.action == "upload")).scalars():
            old.status = "rolled_back" if failed_upload else "reclaimed"
        for old in session.execute(select(FileOperation).where(FileOperation.asset_id == asset.id,
                                    FileOperation.action == "reclaim",
                                    FileOperation.status.in_(["pending", "retained"]))).scalars():
            old.status = "deleted"
    return True


def recover_file_operations():
    """Explicit maintenance only; factory/import never performs recovery."""
    from datetime import timedelta
    from models import utcnow
    cutoff = utcnow() - timedelta(seconds=current_app.config["FILE_OPERATION_GRACE_SECONDS"])
    with Session(db.engine) as session:
        preparations = list(session.execute(select(FileAsset.id).where(FileAsset.state.in_(["staging", "ready", "retained"]),
                                           FileAsset.created_at < cutoff)).scalars())
        reclaims = list(session.execute(select(FileOperation.asset_id).join(FileAsset)
                        .where(FileOperation.action == "reclaim", FileOperation.status.in_(["pending", "retained"]),
                               FileOperation.created_at < cutoff, FileAsset.state != "deleted").distinct()).scalars())
    # A persisted removal operation, unlike an abandoned upload, authorizes a
    # retry against an active asset. reclaim_asset still locks the asset first
    # and checks current references and unknown ownership before any unlink.
    candidates = {asset_id: True for asset_id in preparations}
    candidates.update({asset_id: False for asset_id in reclaims})
    results = {"reclaimed": 0, "retained": 0}
    for asset_id, failed_upload in sorted(candidates.items()):
        try:
            success = reclaim_asset(asset_id, failed_upload=failed_upload)
        except Exception:
            success = False
        results["reclaimed" if success else "retained"] += 1
    return results
