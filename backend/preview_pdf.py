"""Layout-faithful preview PDFs rendered with headless LibreOffice.

Word and RTF originals keep their exact pagination, tables and positioning
when LibreOffice renders them into a PDF, which the existing pdf.js reader
then displays. (PPTX keeps its bespoke data-only slide reader; PDF is already
native; Markdown/HTML/TXT render semantically.)

Generated files are a *regenerable cache* keyed by the SHA-256 of the
immutable original — never registry FileAssets — served only through the
lesson permission model, and the app transparently falls back to the stored
semantic HTML whenever a preview is missing or fails. Rendering is lazy: the
first reader request for a lesson generates its preview under a global lock
and every later request serves the cache, so existing toolkits need no
backfill. A preview can never be fetched for a lesson whose current original
hash differs from the cache key, so replaced or deleted originals leave no
reachable leak (stale same-lesson files are pruned on the next render).

Constraints on the shared production box (~3.4 GB RAM): one conversion at a
time behind an exclusive flock, a hard timeout with process-group kill, the
helper runs unprivileged (setpriv to nobody) when gunicorn is root, and only
a sanitized environment is passed.
"""
import fcntl
import hashlib
import os
import pwd
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from flask import current_app

_MAX_SOURCE = 25 * 1024 * 1024
# Stay safely below gunicorn's request timeout (120s): bounded queue wait for
# the global lock plus a bounded conversion, then a clean 422 text fallback.
_LOCK_WAIT = int(os.environ.get("PREVIEW_PDF_LOCK_WAIT", "30"))
_CONVERT_TIMEOUT = int(os.environ.get("PREVIEW_PDF_TIMEOUT", "60"))


class PreviewUnavailable(Exception):
    """The original exists but cannot be safely read for rendering."""


def preview_dir() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"]) / "preview_pdf"


def _digest(value) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def preview_path(lesson_id: int, source_sha: str) -> Path:
    if _digest(source_sha) is None:
        raise ValueError("Invalid source digest")
    return preview_dir() / f"lesson_{int(lesson_id)}_{source_sha[:16]}.pdf"


def current_preview(lesson_id: int, source_sha) -> Path | None:
    """The derived PDF for exactly this original, if it already exists."""
    sha = _digest(source_sha)
    if sha is None:
        return None
    try:
        path = preview_path(lesson_id, sha)
    except (ValueError, TypeError):
        return None
    return path if path.is_file() and path.stat().st_size > 0 else None


def read_asset_bytes(asset):
    """Bounded, hash-verified read of an original; raises on unusable input."""
    from file_service import open_asset
    maximum = min(_MAX_SOURCE, int(current_app.config.get("FILE_MAX_SIZE", _MAX_SOURCE)))
    if not asset or int(asset.size or 0) > maximum:
        raise PreviewUnavailable()
    try:
        with open_asset(asset) as stream:
            data = stream.read(maximum + 1)
    except Exception:
        raise PreviewUnavailable() from None
    if len(data) > maximum:
        raise PreviewUnavailable()
    digest = hashlib.sha256(data).hexdigest()
    known = _digest(asset.sha256)
    if known and known != digest:
        raise PreviewUnavailable()
    return data, digest


def _unprivileged_prefix():
    """Drop privileges for the renderer; returns argv prefix or None."""
    if os.geteuid() != 0:
        return None
    user = os.environ.get("PREVIEW_PDF_USER", "nobody")
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return ["false"]  # explicit failure instead of rendering as root
    return ["setpriv", f"--reuid={pw.pw_uid}", f"--regid={pw.pw_gid}", "--clear-groups", "--"]


def _prepared_dirs():
    root = preview_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o775)
    base = Path(tempfile.gettempdir())
    profile = base / "zhihe-lo-profile"
    profile.mkdir(exist_ok=True)
    work = base / "zhihe-lo"
    work.mkdir(exist_ok=True)
    prefix = _unprivileged_prefix()
    if prefix and prefix != ["false"]:
        pw = pwd.getpwnam(os.environ.get("PREVIEW_PDF_USER", "nobody"))
        for d in (profile, work):
            os.chown(d, pw.pw_uid, pw.pw_gid)
        os.chmod(profile, 0o700)
        os.chmod(work, 0o777)
    return profile


def _acquire_lock(lock, budget: float) -> bool:
    """Take the global conversion lock within budget (poll for LOCK_NB).

    A blocking flock could pin a sync worker past the request timeout when
    another conversion is running, so waiting is bounded: on exhaustion the
    caller reports the preview as temporarily unavailable and the reader
    falls back to the text view (a later request or worker fills the cache).
    """
    deadline = time.monotonic() + budget
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)


def convert_original(lesson_id: int, source_bytes: bytes, source_sha: str) -> Path:
    """Render one original into its preview PDF under the global lock."""
    target = preview_path(lesson_id, source_sha)
    if target.is_file() and target.stat().st_size > 0:
        return target
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice not installed")
    profile = _prepared_dirs()
    prefix = _unprivileged_prefix()
    if prefix == ["false"]:
        raise RuntimeError("LibreOffice sandbox user unavailable")
    timeout = _CONVERT_TIMEOUT
    root = preview_dir()
    with open(root / ".convert.lock", "a") as lock:
        if not _acquire_lock(lock, _LOCK_WAIT):
            raise PreviewUnavailable()
        try:
            if target.is_file() and target.stat().st_size > 0:
                return target  # finished by another request while we queued
            stage = Path(tempfile.mkdtemp(prefix=f"job{lesson_id}_", dir=str(Path(tempfile.gettempdir()) / "zhihe-lo")))
            if prefix:
                os.chmod(stage, 0o777)  # unique per attempt; nobody writes inside
            try:
                source = stage / "input.bin"
                source.write_bytes(source_bytes)
                source.chmod(0o644)
                args = [soffice, "--headless", "--invisible", "--norestore",
                        "-env:UserInstallation=file://" + str(profile),
                        "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(stage), str(source)]
                if prefix:
                    args = prefix + args
                env = {"PATH": "/usr/bin:/bin", "HOME": str(stage), "LANG": "C.UTF-8",
                       "TMPDIR": str(stage), "SAL_USE_VCLPLUGIN": "svp"}
                proc = subprocess.Popen(args, env=env, cwd=str(stage), start_new_session=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
                    raise RuntimeError("版式预览生成超时")
                produced = next(iter(sorted(stage.glob("*.pdf"))), None)
                if proc.returncode != 0 or produced is None or produced.stat().st_size == 0:
                    raise RuntimeError("版式预览生成失败")
                partial = target.with_name(target.name + ".part")
                shutil.move(str(produced), str(partial))
                partial.chmod(0o644)
                os.replace(partial, target)
                # Keep only the current rendering of this lesson.
                for stale in root.glob(f"lesson_{lesson_id}_*.pdf"):
                    if stale != target:
                        try:
                            stale.unlink(missing_ok=True)
                        except OSError:
                            pass
                return target
            finally:
                shutil.rmtree(stage, ignore_errors=True)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def render_lesson_preview(lesson, original) -> Path:
    """Resolve (and lazily generate) the derived preview PDF for a lesson.

    Raises PreviewUnavailable when the original cannot be safely read (404 at
    the route, reader falls back to convertedHtml) and RuntimeError on a
    genuine LibreOffice failure (422, same fallback).
    """
    from models import FileAsset
    if isinstance(original, FileAsset):
        if original.state not in {"active", "legacy", "retained"}:
            raise PreviewUnavailable()
        cached = current_preview(lesson.id, original.sha256)
        if cached is not None:
            return cached
        data, digest = read_asset_bytes(original)
        return convert_original(lesson.id, data, digest)
    source = Path(original)
    if not source.is_file() or source.stat().st_size > _MAX_SOURCE:
        raise PreviewUnavailable()
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    cached = current_preview(lesson.id, digest)
    if cached is not None:
        return cached
    return convert_original(lesson.id, data, digest)
