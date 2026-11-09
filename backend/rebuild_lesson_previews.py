"""Rebuild selected private lesson HTML derivatives without changing their originals.

Dry-run by default. A production apply requires a private backup directory outside
uploads and the project; each changed lesson receives a reversible JSON backup.
"""
import argparse
import hashlib
import json
import os
import stat
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select, update

from document_converter import convert_document
from extensions import db
from models import FileReference, Lesson
from file_service import open_asset

FORMATS = {"word": (".docx",), "html": (".html", ".htm"), "richtext": (".rtf",)}


def unchanged_html(value):
    # MySQL's default text collation ignores case and accents; compare UTF-8
    # bytes so an intervening edit cannot pass an optimistic update.
    return (Lesson.converted_html.is_(None) if value is None else
            func.hex(Lesson.converted_html) == value.encode("utf-8").hex().upper())


def digest(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def private_raw(app, lesson):
    expected = FORMATS.get(lesson.content_type)
    if not expected or not lesson.raw_file_path:
        raise ValueError("课程没有可重建的 Word、HTML 或 RTF 原件")
    root = Path(app.config["UPLOAD_FOLDER"]).resolve(strict=True)
    raw_root = (root / "lesson_raw").resolve(strict=True)
    if not raw_root.is_relative_to(root):
        raise ValueError("课程原件目录不在私有上传根目录中")
    original = Path(lesson.raw_file_path)
    if not original.is_absolute() or original.is_symlink():
        raise ValueError("课程原件路径不是有效的绝对路径")
    resolved = original.resolve(strict=True)
    if (resolved.parent != raw_root or resolved.suffix.lower() not in expected or
            not resolved.name.startswith(f"lesson_{lesson.id}_") or not resolved.is_file()):
        raise ValueError("课程原件不属于本课程的私有原件目录")
    if resolved.stat().st_size > app.config["FILE_MAX_SIZE"]:
        raise ValueError("课程原件超过文件大小限制")
    return resolved


def _raw_asset(lesson):
    ref = db.session.execute(select(FileReference).where(
        FileReference.lesson_id == lesson.id, FileReference.toolkit_id == lesson.toolkit_id,
        FileReference.usage == "lesson_raw")).scalar_one_or_none()
    return ref.asset if ref else None


@contextmanager
def raw_source(app, lesson):
    asset = _raw_asset(lesson)
    if not asset:
        yield private_raw(app, lesson)
        return
    expected = FORMATS.get(lesson.content_type)
    suffix = Path(asset.stored_name).suffix.lower()
    if (not expected or suffix not in expected or asset.purpose != "lesson_raw" or
            asset.state not in {"active", "legacy", "retained"} or asset.size > app.config["FILE_MAX_SIZE"]):
        raise ValueError("课程没有可重建的私有原件")
    with tempfile.TemporaryDirectory(prefix="zhihe-raw-preview-") as staging:
        path = Path(staging) / ("original" + suffix)
        with open_asset(asset) as source, path.open("wb") as target:
            shutil.copyfileobj(source, target, 128 * 1024)
        yield path


def _same_raw(lesson_id, asset_id):
    current = select(FileReference.asset_id).where(
        FileReference.lesson_id == lesson_id, FileReference.usage == "lesson_raw").scalar_subquery()
    return current == asset_id if asset_id is not None else current.is_(None)


def _backup_dir(app, directory):
    path = Path(directory).resolve(strict=True)
    upload_root = Path(app.config["UPLOAD_FOLDER"]).resolve(strict=True)
    project_root = Path(__file__).resolve().parent.parent
    if not path.is_dir() or path.is_relative_to(upload_root) or path.is_relative_to(project_root):
        raise ValueError("备份必须位于项目和上传目录以外的已有私有文件夹")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("备份目录权限必须为 0700 或更严格")
    return path


def _save_backup(directory, lesson, old_html, new_html, raw_sha, raw_asset_id):
    name = f"lesson_{lesson.id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid4().hex[:8]}.json"
    path = directory / name
    payload = {
        "lessonId": lesson.id,
        "contentType": lesson.content_type,
        "rawFilePath": lesson.raw_file_path,
        "rawAssetId": raw_asset_id,
        "rawSha256": raw_sha,
        "oldHtml": old_html,
        "newSha256": digest(new_html),
    }
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False)
    return path


def rebuild(app, ids, apply=False, backup_dir=None):
    with app.app_context():
        directory = _backup_dir(app, backup_dir) if apply else None
        failed = 0
        for lesson_id in dict.fromkeys(ids):
            try:
                lesson = db.session.get(Lesson, lesson_id)
                if lesson is None:
                    raise ValueError("找不到课程")
                asset = _raw_asset(lesson)
                raw_asset_id = asset.id if asset else None
                original = lesson.converted_html
                with raw_source(app, lesson) as raw:
                    raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
                    updated = convert_document(str(raw), lesson.content_type)
                    if hashlib.sha256(raw.read_bytes()).hexdigest() != raw_sha:
                        raise ValueError("原件在转换期间发生变化，拒绝写入")
                if not updated:
                    raise ValueError("转换结果为空，已保留原有展示")
                if updated == original:
                    print(f"{lesson_id}: unchanged ({digest(original)[:12]})")
                    continue
                print(f"{lesson_id}: {digest(original)[:12]} -> {digest(updated)[:12]}"
                      f" ({len(original or '')} -> {len(updated)} characters)")
                if not apply:
                    continue
                backup_path = _save_backup(directory, lesson, original, updated, raw_sha, raw_asset_id)
                result = db.session.execute(update(Lesson).where(
                    Lesson.id == lesson_id,
                    Lesson.raw_file_path == lesson.raw_file_path,
                    _same_raw(lesson_id, raw_asset_id),
                    Lesson.content_type == lesson.content_type,
                    unchanged_html(original),
                ).values(converted_html=updated))
                if result.rowcount != 1:
                    db.session.rollback()
                    raise ValueError("课程在转换期间已被修改，拒绝覆盖")
                db.session.commit()
                print(f"{lesson_id}: applied; restore from {backup_path}")
            except Exception as exc:
                db.session.rollback()
                failed += 1
                print(f"{lesson_id}: FAILED: {exc}")
        return failed


def restore(app, backup_file):
    with app.app_context():
        data = json.loads(Path(backup_file).read_text(encoding="utf-8"))
        lesson = db.session.get(Lesson, data["lessonId"])
        if lesson is None or lesson.content_type != data["contentType"] or lesson.raw_file_path != data["rawFilePath"]:
            raise ValueError("备份与当前课程不匹配")
        asset = _raw_asset(lesson)
        raw_asset_id = asset.id if asset else None
        if raw_asset_id != data.get("rawAssetId"):
            raise ValueError("课程原件引用发生变化，不能自动回滚")
        with raw_source(app, lesson) as raw:
            if hashlib.sha256(raw.read_bytes()).hexdigest() != data["rawSha256"]:
                raise ValueError("原件发生变化，不能自动回滚")
        current = lesson.converted_html
        if digest(current) != data["newSha256"]:
            raise ValueError("派生内容发生变化，不能覆盖后续编辑")
        result = db.session.execute(update(Lesson).where(
            Lesson.id == lesson.id,
            Lesson.raw_file_path == data["rawFilePath"],
            _same_raw(lesson.id, raw_asset_id),
            Lesson.content_type == data["contentType"],
            unchanged_html(current),
        ).values(converted_html=data["oldHtml"]))
        if result.rowcount != 1:
            db.session.rollback()
            raise ValueError("课程在回滚期间发生变化")
        db.session.commit()
        print(f"{lesson.id}: restored from {backup_file}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", type=int, nargs="+", help="明确指定需要重建的课程 ID")
    parser.add_argument("--apply", action="store_true", help="备份后写入派生 HTML；默认仅预览")
    parser.add_argument("--backup-dir", type=Path, help="已有的权限 0700 备份目录，不能在项目内")
    parser.add_argument("--restore", type=Path, help="仅回滚指定的 JSON 备份文件")
    args = parser.parse_args()
    if args.restore:
        if args.ids or args.apply or args.backup_dir:
            parser.error("--restore 不能与 --ids/--apply/--backup-dir 同时使用")
    elif not args.ids or (args.apply and not args.backup_dir):
        parser.error("重建需传 --ids；--apply 还需传 --backup-dir")
    from app import create_app
    application = create_app()
    if args.restore:
        restore(application, args.restore)
    elif rebuild(application, args.ids, args.apply, args.backup_dir):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
