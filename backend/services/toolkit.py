"""Toolkit validation, asset preparation, persistence, and publishing logic."""
import json
import os
import tempfile
import uuid
from pathlib import Path
from contextlib import nullcontext

from flask import current_app
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload, undefer
from werkzeug.utils import secure_filename

from extensions import db
from models import (ASSET_PATH, LESSON_ASSET_PATH, FileReference, Lesson, LessonProgress, Toolkit,
                    User, utcnow)
from security import APIError, assert_identity, positive_id
from access import is_manager
import money_service as money
from document_converter import convert_document, detect_content_type
from file_service import (bind_reference, lock_assets, reclaim_asset, referenced_asset_ids,
                          init_storage, safe_stored_name, source_asset, unbind_toolkit)
from validation import CATEGORIES, LEVELS, MAX_LESSON_BYTES, MAX_PARSED_TEXT_BYTES, parse_money, string_field


def toolkit_form_errors(form, allow_incomplete=False):
    try:
        title = string_field(form.get("title", ""), "标题", 120, not allow_incomplete)
        description = string_field(form.get("description", ""), "介绍", 20000, not allow_incomplete)
        summary = string_field(form.get("summary", ""), "摘要", 500) or description[:120]
        category = string_field(form.get("category", "生信分析"), "分类", 50)
        level = string_field(form.get("level", "入门"), "难度", 20)
        if category not in CATEGORIES or level not in LEVELS:
            raise ValueError("分类或难度无效")
        raw_price = form.get("price", 0)
        if allow_incomplete and raw_price == "":
            raw_price = 0
        price = parse_money(raw_price, 0, 9999, "定价")
        tags = string_field(form.get("tags", ""), "标签", 300)
        cover = string_field(form.get("cover", ""), "封面", 500) or None
        return None, {"title": title, "description": description, "summary": summary,
                      "category": category, "level": level, "price": price, "tags": tags, "cover": cover}
    except ValueError as error:
        return str(error), None


def parse_lessons_json(raw, allow_incomplete=False):
    try:
        if raw is None or raw == "":
            lessons = []
        elif isinstance(raw, str):
            if len(raw.encode("utf-8")) > 20 * 1024 * 1024:
                raise ValueError("教程正文总量过大")
            lessons = json.loads(raw)
        else:
            lessons = raw
        if not isinstance(lessons, list) or len(lessons) > 50:
            raise ValueError("教程步骤须为不超过 50 项的数组")
        cleaned = []
        total = 0
        for index, lesson in enumerate(lessons):
            if not isinstance(lesson, dict):
                raise ValueError(f"步骤 {index + 1} 格式不正确")
            title = string_field(lesson.get("title", f"步骤 {index + 1}"), "步骤标题", 160, not allow_incomplete)
            content = lesson.get("content", "")
            if not isinstance(content, str) or len(content.encode("utf-8")) > 1024 * 1024:
                raise ValueError("步骤正文须为不超过 1MB 的字符串")
            total += len(content.encode("utf-8"))
            if total > 20 * 1024 * 1024:
                raise ValueError("教程正文总量过大")
            image_value = lesson.get("image", "")
            image = string_field("" if image_value is None else image_value, "步骤图片", 500) or None
            duration = lesson.get("duration", 10)
            draft_duration_placeholder = allow_incomplete and type(duration) is int and duration == 0
            if not draft_duration_placeholder and (type(duration) is not int or not 1 <= duration <= 600):
                raise ValueError("学习时长须为 1～600 的整数")
            preview = lesson.get("preview", lesson.get("isPreview", index == 0))
            if type(preview) is not bool:
                raise ValueError("试看须为 true/false")
            lesson_id = positive_id(lesson["id"], "步骤ID") if "id" in lesson else None
            clear_document = lesson.get("clearDocument", False)
            if type(clear_document) is not bool:
                raise ValueError("clearDocument 必须是 true/false")
            if clear_document and not content.strip():
                raise ValueError("清除教程原件时须提供新的非空正文")
            declared_type = lesson.get("contentType", lesson.get("content_type"))
            if ("contentType" in lesson and "content_type" in lesson
                    and lesson["contentType"] != lesson["content_type"]):
                raise ValueError("步骤文档类型冲突")
            if declared_type is not None and (not isinstance(declared_type, str) or
                                              declared_type not in {"markdown", "word", "pdf", "html", "richtext", "ppt"}):
                raise ValueError("步骤文档类型无效")
            cleaned.append({"id": lesson_id, "title": title, "content": content,
                            "_content_omitted": "content" not in lesson, "image_url": image,
                            "image_file": None, "duration": duration, "preview": preview,
                            "content_type": declared_type, "_clear_document": clear_document})
        return None, cleaned
    except (ValueError, TypeError) as error:
        return str(error), None


def validate_toolkit_completeness(title, description, lessons):
    if not title.strip():
        raise APIError("请填写工具包名称")
    if not description.strip():
        raise APIError("请填写工具包介绍")
    if not lessons:
        raise APIError("请至少添加一个教程步骤")
    for index, (lesson_title, duration, content, has_document) in enumerate(lessons):
        if not lesson_title.strip():
            raise APIError(f"请填写第 {index + 1} 步的标题")
        if type(duration) is not int or not 1 <= duration <= 600:
            raise APIError(f"第 {index + 1} 步的学习时长须为 1～600 分钟的整数")
        if not isinstance(content, str) or not content.strip():
            if not has_document:
                raise APIError(f"请填写第 {index + 1} 步正文，或上传教程原件")


def _canonical_lesson_image(value):
    if value and value.startswith(LESSON_ASSET_PATH):
        stored = value[len(LESSON_ASSET_PATH):]
        if safe_stored_name(stored):
            return "file:" + stored
    return value


def _prepare_upload(uploads, kind, data):
    if not data:
        return None
    if "stream" not in data:
        return uploads.descriptor(kind, data)
    stream = data["stream"]
    position = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell() - position
    stream.seek(position)
    return uploads.prepare(kind, data["name"], stream, size)


def _prepare_item_assets(item, uploads):
    cover = _prepare_upload(uploads, "image", item.get("cover_file"))
    resources = [uploads.descriptor("resource", descriptor) for descriptor in (item.get("resource_files") or [])]
    for stream, name in (item.get("resource_streams") or []):
        # Multipart is already streamed into Werkzeug's private spool; don't
        # materialize it as bytes just to determine its length.
        position = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell() - position
        stream.seek(position)
        resources.append(uploads.prepare("resource", name, stream, size))
    images = [_prepare_upload(uploads, "asset", lesson.get("image_file")) for lesson in (item["lessons"] or [])]
    return cover, [item for item in resources if item], images


def _reclaim_lesson_raw(lesson_id, filename):
    """Remove an unreferenced private original only after checking the committed DB."""
    if not filename:
        return
    base = (Path(current_app.config["UPLOAD_FOLDER"]) / "lesson_raw").resolve(strict=True)
    path = Path(filename)
    if (not path.is_absolute() or not path.name.startswith(f"lesson_{lesson_id}_") or
            not detect_content_type(path.name) or path.is_symlink() or
            path.parent.resolve() != base or path.resolve().parent != base):
        current_app.logger.warning("Skipped unsafe lesson original cleanup for lesson %s", lesson_id)
        return
    with Session(db.engine) as check:
        referenced = check.execute(select(Lesson.id).where(Lesson.raw_file_path == str(path)).limit(1)).first()
    if not referenced:
        path.unlink(missing_ok=True)


def _prepare_raw_documents(item, uploads):
    """Convert bounded private originals before the business transaction locks the owner."""
    prepared = []
    rendered_total = 0
    for index, lesson in enumerate(item["lessons"] or []):
        file = lesson.get("_file")
        filename = file.get("name") if isinstance(file, dict) else getattr(file, "filename", None)
        if not file or not filename:
            prepared.append(None)
            continue
        kind = detect_content_type(filename)
        if not kind:
            raise APIError(f"第 {index + 1} 步文档格式不受支持")
        if lesson.get("content_type") is not None and lesson["content_type"] != kind:
            raise APIError(f"第 {index + 1} 步文档类型与上传文件不一致")
        init_storage()
        path = None
        try:
            source = open(file["path"], "rb") if isinstance(file, dict) else nullcontext(file.stream)
            with source as stream, tempfile.NamedTemporaryFile(
                    dir=Path(current_app.config["UPLOAD_FOLDER"]) / ".staging",
                    suffix=Path(filename).suffix.lower(), delete=False) as output:
                path = Path(output.name)
                size = 0
                while chunk := stream.read(128 * 1024):
                    size += len(chunk)
                    if size > current_app.config["FILE_MAX_SIZE"]:
                        raise APIError("文档超过单文件大小限制", 413)
                    output.write(chunk)
            if kind == "markdown" and size > MAX_LESSON_BYTES:
                raise APIError("Markdown 原件正文超过 1MB")
            converted = convert_document(path, kind)
            if len(converted) > 4 * MAX_LESSON_BYTES:
                raise APIError("转换后的文档预览超过 4MB")
            rendered_size = len(converted.encode("utf-8"))
            if rendered_size > 4 * MAX_LESSON_BYTES:
                raise APIError("转换后的文档预览超过 4MB")
            rendered_total += rendered_size
            if rendered_total > MAX_PARSED_TEXT_BYTES:
                raise APIError("教程转换后的预览总量超过 20MB")
            body = path.read_text(encoding="utf-8") if kind == "markdown" else f"[Document: {filename}]"
            asset = uploads.prepare("lesson_raw", filename, path, size,
                                    cache_key=file.get("cache_key") if isinstance(file, dict) else None)
            prepared.append({**asset, "content_type": kind, "converted_html": converted, "content": body})
        except APIError as error:
            raise APIError(f"第 {index + 1} 步文档原件：{error.message}", error.status, error.code) from error
        except Exception:
            current_app.logger.exception("Document conversion failed")
            raise APIError(f"第 {index + 1} 步文档解析失败，请检查文件格式与内容") from None
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
    return prepared


def persist_item(item, identity, uploads, toolkit_id=None, skip_existing=False, publish=True,
                 reclaim_asset_fn=None):
    """Single/batch shared import with a fresh transaction per item."""
    if skip_existing:
        # Detect existing titles before any filesystem work/quota reservation.
        # The final check under the same owner lock handles racing new titles.
        with Session(db.engine) as preflight, preflight.begin():
            owner = money.lock_users(preflight, [identity.uid])[identity.uid]
            assert_identity(preflight, owner, identity)
            existing = preflight.execute(select(Toolkit.id).where(Toolkit.author_id == owner.id,
                       Toolkit.title == item["title"]).order_by(Toolkit.id).limit(1)).scalar_one_or_none()
            if existing:
                return {"toolkitId": existing, "status": "skipped"}
    if toolkit_id:
        # Reject an unauthorized edit before converting or storing its attachments.
        # The locked business transaction repeats the check after this preflight.
        with Session(db.engine) as preflight:
            existing = preflight.get(Toolkit, toolkit_id)
            if not existing:
                raise APIError("工具包不存在", 404)
            owner = preflight.get(User, identity.uid)
            assert_identity(preflight, owner, identity)
            if not is_manager(existing, owner):
                raise APIError("只能编辑自己发布的工具包", 403)
    cover, resources, images = _prepare_item_assets(item, uploads)
    raw_documents = _prepare_raw_documents(item, uploads)
    allocated = {x["id"] for x in [cover, *resources, *images, *raw_documents] if x}
    created_id = None
    stale_ids = []
    created_raw = []
    retired_raw = []
    try:
        with Session(db.engine, expire_on_commit=False) as session, session.begin():
            retained_resources = []
            retained_raw = {}
            if toolkit_id:
                toolkit = money.lock_toolkit(session, toolkit_id)
                if not toolkit:
                    raise APIError("工具包不存在", 404)
                money.lock_orders(session, toolkit_id)
                stale_ids = referenced_asset_ids(session, toolkit_id)
                # Old attachment names survive a replacement-free edit.
                retained_resources = [(row.asset_id, row.name, row.sort_order, row.id) for row in
                                      session.execute(select(FileReference).where(
                                          FileReference.toolkit_id == toolkit_id,
                                          FileReference.usage == "resource")).scalars()]
                retained_raw = {row.lesson_id: row.asset_id for row in session.execute(
                    select(FileReference).where(FileReference.toolkit_id == toolkit_id,
                                                FileReference.usage == "lesson_raw")).scalars()}
            else:
                toolkit = None
            old_cover = toolkit.cover if toolkit else None
            old_lessons = list(session.execute(select(Lesson).where(Lesson.toolkit_id == toolkit_id)
                               .options(undefer(Lesson.content)).order_by(Lesson.sort_order, Lesson.id)
                               .execution_options(populate_existing=True)).scalars()) if toolkit else []
            owner = money.lock_users(session, [identity.uid])[identity.uid]
            assert_identity(session, owner, identity)
            if toolkit and not is_manager(toolkit, owner):
                raise APIError("只能编辑自己发布的工具包", 403)
            if toolkit and "_partial_fields" in item:
                merged = {field: getattr(toolkit, field) for field in
                          ["title", "summary", "description", "category", "level", "price", "tags", "cover"]}
                merged["cover"] = merged["cover"] or ""
                merged.update(item["_partial_fields"])
                if merged.get("cover") is None:
                    merged["cover"] = ""
                error, fields = toolkit_form_errors(merged)
                if error:
                    raise APIError(error)
                item.update(fields)
                item["cover_url"] = fields["cover"]
            if item["lessons"] is None:
                item["lessons"] = [{"id": lesson.id, "title": lesson.title, "content": lesson.content,
                                    "image_url": lesson.image, "image_file": None, "duration": lesson.duration,
                                    "preview": lesson.is_preview} for lesson in old_lessons]
                images = [None] * len(old_lessons)
                raw_documents = [None] * len(old_lessons)
            if skip_existing:
                # Owner lock is a stable synchronization point for this
                # author's batch duplicate-title check (non-financial path).
                existing = session.execute(select(Toolkit.id).where(Toolkit.author_id == owner.id,
                           Toolkit.title == item["title"]).order_by(Toolkit.id).limit(1)).scalar_one_or_none()
                if existing:
                    return {"toolkitId": existing, "status": "skipped"}
            if not toolkit:
                new_status = item.get("status") or ("published" if publish else "draft")
                toolkit = Toolkit(slug=f"{uuid.uuid4().hex[:12]}-{secure_filename(item['title'])[:50] or 'toolkit'}",
                                  author_id=owner.id, status=new_status, featured=False)
                session.add(toolkit)
            for field in ["title", "summary", "description", "category", "level", "price", "tags"]:
                setattr(toolkit, field, item[field])
            session.flush()
            created_id = toolkit.id
            cover_url = ASSET_PATH + cover["stored_name"] if cover else (
                old_cover if toolkit_id and item.get("_cover_omitted") else item.get("cover_url"))
            preserve_cover = bool(toolkit_id and not cover and cover_url == old_cover)
            cover_id = cover["id"] if cover else source_asset(session, cover_url, identity, "cover", preserve_unknown=preserve_cover)
            lesson_ids, kept_ids = [], set()
            old_by_id = {lesson.id: lesson for lesson in old_lessons}
            explicit_ids = {data["id"] for data in item["lessons"] if data.get("id")}
            for index, (lesson, prepared) in enumerate(zip(item["lessons"], images)):
                candidate = old_lessons[index] if index < len(old_lessons) else None
                previous = (old_by_id.get(lesson["id"]) if lesson.get("id") else
                            candidate if candidate and candidate.id not in explicit_ids else None)
                if lesson.get("id") and previous is None:
                    raise APIError("步骤ID不属于此工具包")
                if previous:
                    if previous.id in kept_ids:
                        raise APIError("步骤ID不能重复")
                    kept_ids.add(previous.id)
                url = ("file:" + prepared["stored_name"] if prepared else _canonical_lesson_image(
                    previous.image if previous and lesson.get("_image_omitted") else lesson.get("image_url")))
                # Preservation is scoped to this exact prior Lesson, never a
                # toolkit-wide set of private images or another toolkit's ID.
                preserved = bool(previous and not prepared and url == _canonical_lesson_image(previous.image))
                asset_id = prepared["id"] if prepared else source_asset(session, url, identity, "lesson", preserve_unknown=preserved)
                lesson_ids.append((url, asset_id, previous, preserved))
            if toolkit.status == "published":
                validate_toolkit_completeness(toolkit.title, toolkit.description, [
                    (data["title"], data["duration"],
                     previous.content if previous and "_partial_fields" in item and data.get("_content_omitted")
                     else data["content"],
                     bool(raw_documents[index] or (previous and not data.get("_clear_document") and
                          (previous.raw_file_path or previous.id in retained_raw))))
                    for index, (data, (_, _, previous, _)) in enumerate(zip(item["lessons"], lesson_ids))
                ])
            asset_ids = [x for x in [cover_id, *[r["id"] for r in resources],
                                     *[item[1] for item in lesson_ids],
                                     *[raw["id"] for raw in raw_documents if raw], *stale_ids,
                                     *[row[0] for row in retained_resources], *retained_raw.values()] if x]
            assets = lock_assets(session, asset_ids)
            if toolkit_id:
                preserved_resource_ids = []
                if not resources:
                    for asset_id, _, _, reference_id in retained_resources:
                        retained = assets.get(asset_id)
                        if retained:
                            if retained.state not in {"ready", "active", "retained", "legacy"}:
                                raise APIError("文件不可用", 409)
                            preserved_resource_ids.append(reference_id)
                unbind_toolkit(session, toolkit.id, {asset_id: assets[asset_id] for asset_id in set(stale_ids)},
                               preserved_reference_ids=preserved_resource_ids)
                removed_ids = set(old_by_id) - kept_ids
                retired_raw.extend((lesson_id, old_by_id[lesson_id].raw_file_path) for lesson_id in removed_ids
                                    if old_by_id[lesson_id].raw_file_path)
                if removed_ids:
                    session.execute(delete(LessonProgress).where(LessonProgress.lesson_id.in_(removed_ids)))
                    session.execute(delete(Lesson).where(Lesson.id.in_(removed_ids)))
            toolkit.cover = cover_url
            if cover_id:
                bind_reference(session, assets[cover_id], identity, toolkit.id, "cover", preserve=preserve_cover)
            if resources:
                for position, resource in enumerate(resources):
                    bind_reference(session, assets[resource["id"]], identity, toolkit.id, "resource",
                                   name=resource["name"], sort_order=position,
                                   binding_key=f"toolkit:{toolkit.id}:resource:{position}:{resource['id']}")
                toolkit.resource_name, toolkit.resource_path = resources[0]["name"], resources[0]["stored_name"]
            for index, (data, (url, asset_id, previous, preserved_image)) in enumerate(zip(item["lessons"], lesson_ids)):
                raw = raw_documents[index]
                # Platform images always use the protected policy route. Public
                # shared images keep their underlying image-directory purpose.
                if asset_id:
                    url = "file:" + assets[asset_id].stored_name
                lesson = previous or Lesson(toolkit_id=toolkit.id)
                body = (previous.content if previous and "_partial_fields" in item and data.get("_content_omitted")
                        else data["content"])
                if (previous and not raw and not data.get("_clear_document")
                        and (previous.content_type or "markdown") != "markdown"
                        and (previous.raw_file_path or previous.id in retained_raw)
                        and body != previous.content):
                    raise APIError(f"第 {index + 1} 步保留非 Markdown 原件时不可单独修改正文；请上传新原件或清除旧原件")
                lesson.title, lesson.content, lesson.image = data["title"], body, url
                lesson.duration, lesson.sort_order, lesson.is_preview = data["duration"], index, data["preview"]

                declared_type = data.get("content_type")
                if raw:
                    lesson.content_type = raw["content_type"]
                elif data.get("_clear_document"):
                    lesson.content_type = "markdown"
                    lesson.converted_html = None
                    if previous and previous.raw_file_path:
                        retired_raw.append((lesson.id, previous.raw_file_path))
                    lesson.raw_file_path = None
                else:
                    # Metadata-only edits retain the original and its converted preview.
                    existing_type = previous.content_type if previous else "markdown"
                    if declared_type is not None and declared_type != existing_type:
                        raise APIError("步骤文档类型不可修改，请上传对应格式的文件")
                    lesson.content_type = existing_type

                session.add(lesson)
                session.flush()

                if raw:
                    if previous and previous.raw_file_path:
                        retired_raw.append((lesson.id, previous.raw_file_path))
                    lesson.raw_file_path = None
                    lesson.converted_html = raw["converted_html"]
                    lesson.content = data["content"] if data["content"].strip() else raw["content"]
                    bind_reference(session, assets[raw["id"]], identity, toolkit.id, "lesson_raw",
                                   lesson.id, name=raw["name"])
                elif previous and previous.id in retained_raw and not data.get("_clear_document"):
                    raw_id = retained_raw[previous.id]
                    bind_reference(session, assets[raw_id], identity, toolkit.id, "lesson_raw",
                                   lesson.id, preserve=True)

                if asset_id:
                    bind_reference(session, assets[asset_id], identity, toolkit.id, "lesson", lesson.id, preserve=preserved_image)
            session.flush()
        # Query with the normal request session only after independent commit.
        db.session.rollback()  # Discard the JWT request's earlier RR snapshot.
        toolkit = db.session.execute(select(Toolkit).where(Toolkit.id == created_id).options(
                  undefer(Toolkit.description), selectinload(Toolkit.lessons).undefer(Lesson.content))).scalar_one()
        result = toolkit.serialize(identity.uid, detail=True)
    except Exception:
        # An acknowledgement failure can happen after COMMIT. Check using a
        # NEW connection, not the rolled-back/stale request snapshot.
        confirmed = False
        if created_id and not toolkit_id:
            try:
                with Session(db.engine) as check:
                    confirmed = check.execute(select(Toolkit.id).where(Toolkit.id == created_id,
                                Toolkit.author_id == identity.uid)).scalar_one_or_none() is not None
            except SQLAlchemyError:
                pass
        if confirmed:
            db.session.rollback()
            toolkit = db.session.get(Toolkit, created_id)
            result = toolkit.serialize(identity.uid, detail=True)
        else:
            for lesson_id, raw_path in created_raw:
                try:
                    _reclaim_lesson_raw(lesson_id, raw_path)
                except (OSError, SQLAlchemyError):
                    current_app.logger.warning("Deferred uncommitted lesson original cleanup for lesson %s", lesson_id)
            uploads.resolve_failure(allocated)
            raise
    for lesson_id, raw_path in retired_raw:
        try:
            _reclaim_lesson_raw(lesson_id, raw_path)
        except (OSError, SQLAlchemyError):
            current_app.logger.warning("Deferred old lesson original cleanup for lesson %s", lesson_id)
    reclaimer = (reclaim_asset_fn or current_app.extensions.get("zhihe.reclaim_asset")
                or reclaim_asset)
    for asset_id in sorted(set(stale_ids)):
        try:
            reclaimer(asset_id)
        except Exception:
            current_app.logger.warning("Committed old-file cleanup deferred for asset %s", asset_id)
    return result


__all__ = ["toolkit_form_errors", "parse_lessons_json", "validate_toolkit_completeness",
           "_canonical_lesson_image", "_prepare_item_assets", "_reclaim_lesson_raw", "persist_item"]
