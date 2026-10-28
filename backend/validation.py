"""Shared, standard-library-only validation for API money and batch packages."""
import math
import ntpath
import posixpath
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from urllib.parse import urlsplit

MAX_BATCH_ITEMS = 50
MAX_LESSONS = 50
MAX_ITEM_BYTES = 40 * 1024 * 1024
MAX_LESSON_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_PARSED_TEXT_BYTES = 20 * 1024 * 1024
MAX_ZIP_COMPRESSED = 100 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED = 300 * 1024 * 1024
MAX_ZIP_MEMBERS = 2000
MAX_DESCRIPTION_CHARS = 20000
MAX_PATH_CHARS = 500
MAX_FILENAME_CHARS = 200
MANIFEST_NAME = "toolkits.json"
CATEGORIES = ["生信分析", "单细胞", "空间组学", "结构生物学", "基因编辑"]
LEVELS = ["入门", "进阶", "专业"]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
REJECTED_RESOURCE_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".scr", ".vbs", ".jse", ".wsf", ".ps1", ".dll", ".msi", ".hta"}


def parse_money(value, minimum, maximum, field="金额") -> Decimal:
    """Return finite, bounded money rounded to cents; malformed values raise ValueError."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{field}必须是有效数字")
    if isinstance(value, str) and (not value.strip() or len(value) > 100):
        raise ValueError(f"{field}必须是有效数字")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field}必须是有限数字")
    try:
        amount = Decimal(str(value).strip())
        low, high = Decimal(str(minimum)), Decimal(str(maximum))
        if not amount.is_finite() or not low.is_finite() or not high.is_finite():
            raise ValueError(f"{field}必须是有限数字")
        if low > high or amount < low or amount > high:
            raise ValueError(f"{field}须在 {minimum}～{maximum} 之间")
        with localcontext() as context:
            context.prec = max(28, len(amount.as_tuple().digits) + 2)
            result = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if result < low or result > high:
            raise ValueError(f"{field}须在 {minimum}～{maximum} 之间")
        return result
    except (InvalidOperation, OverflowError, TypeError) as exc:
        raise ValueError(f"{field}必须是有效数字") from exc


def string_field(value, field, maximum, required=False):
    if not isinstance(value, str):
        raise ValueError(f"{field}必须是字符串")
    # Check before trimming: oversized whitespace is not a way around budgets.
    if len(value) > maximum:
        raise ValueError(f"{field}不能超过 {maximum} 字")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field}不能为空")
    return value


def normalize_relative_path(value, field="路径"):
    value = string_field(value, field, MAX_PATH_CHARS, required=True)
    if (value.startswith("/") or "\\" in value or "\x00" in value
            or ntpath.splitdrive(value)[0] or ":" in value
            or ".." in value.split("/")):
        raise ValueError(f"{field}必须是安全的相对路径")
    normalized = posixpath.normpath(value)
    if normalized in ("", "."):
        raise ValueError(f"{field}必须指向文件")
    if any(len(part) > MAX_FILENAME_CHARS for part in normalized.split("/")):
        raise ValueError(f"{field}文件名不能超过 {MAX_FILENAME_CHARS} 字")
    return normalized


def image_source(value, field):
    value = string_field(value, field, MAX_PATH_CHARS)
    if not value:
        return None, None
    if value.lower().startswith(("http://", "https://")):
        try:
            parsed = urlsplit(value)
            port = parsed.port
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or any(ch.isspace() or ord(ch) < 32 for ch in value) or "\\" in value):
                raise ValueError("invalid URL")
            if port is not None and not 1 <= port <= 65535:
                raise ValueError("invalid port")
        except ValueError as exc:
            raise ValueError(f"{field}图片 URL 不合法") from exc
        return value, None
    rel = normalize_relative_path(value, field)
    if posixpath.splitext(rel)[1].lower() not in IMAGE_EXTENSIONS:
        raise ValueError(f"{field}仅支持 {'/'.join(sorted(IMAGE_EXTENSIONS))}")
    return None, rel


def resource_source(value, field="resource"):
    value = string_field(value, field, MAX_PATH_CHARS)
    if not value:
        return None
    rel = normalize_relative_path(value, field)
    if posixpath.splitext(rel)[1].lower() in REJECTED_RESOURCE_EXTENSIONS:
        raise ValueError(f"{field}不能是可执行文件类型")
    return rel


def manifest_items(data):
    """Accept an array or the legacy {toolkits: [...]} wrapper, never scalar JSON."""
    if isinstance(data, dict):
        data = data.get("toolkits")
    if not isinstance(data, list) or not data:
        raise ValueError("toolkits.json 必须是非空 JSON 数组或含 toolkits 数组的对象")
    if len(data) > MAX_BATCH_ITEMS:
        raise ValueError(f"一次最多上传 {MAX_BATCH_ITEMS} 个工具包")
    return data


class TextBudget:
    """Bound retained/parsed text; identical file text is charged only once."""
    def __init__(self, maximum=MAX_PARSED_TEXT_BYTES):
        self.maximum = maximum
        self.used = 0
        self.keys = set()

    def charge(self, size, key=None):
        if key is not None and key in self.keys:
            return
        if self.used + size > self.maximum:
            raise ValueError(f"解析文本总量超过 {self.maximum // (1024 * 1024)}MB")
        self.used += size
        if key is not None:
            self.keys.add(key)


def normalize_batch_item(raw, index, resolver, text_budget):
    """Validate one raw item. resolver supplies asset(rel, field)/text(rel, field).

    Assets are dictionaries with name/path/size/cache_key. text() returns cached,
    trimmed UTF-8 text and charges the same TextBudget used here.
    """
    if not isinstance(raw, dict):
        raise ValueError("条目必须是 JSON 对象")
    title = string_field(raw.get("title"), "title", 120, required=True)
    description = string_field(raw.get("description"), "description", MAX_DESCRIPTION_CHARS, required=True)
    summary = string_field(raw.get("summary", ""), "summary", 500) or description[:500]
    category = string_field(raw.get("category", CATEGORIES[0]), "category", 50, required=True)
    level = string_field(raw.get("level", LEVELS[0]), "level", 20, required=True)
    if category not in CATEGORIES:
        raise ValueError(f"分类须为：{' / '.join(CATEGORIES)}")
    if level not in LEVELS:
        raise ValueError(f"难度须为：{' / '.join(LEVELS)}")
    price = parse_money(raw.get("price", 0), 0, 9999, "定价")
    tags = raw.get("tags", "")
    if isinstance(tags, list):
        if len(tags) > 100 or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("tags 必须是字符串或不超过 100 项的字符串数组")
        tags = ",".join(tag.strip() for tag in tags if tag.strip())
    tags = string_field(tags, "tags", 300)
    if "featured" in raw and not isinstance(raw["featured"], bool):
        raise ValueError("featured 必须是 true/false（该字段不授予推荐位）")
    source_lessons = raw.get("lessons", [])
    if not isinstance(source_lessons, list) or len(source_lessons) > MAX_LESSONS:
        raise ValueError(f"lessons 必须是不超过 {MAX_LESSONS} 项的数组")
    text_budget.charge(sum(len(text.encode("utf-8")) for text in (title, description, summary, tags)))
    # Validate every lesson/source before doing any asset I/O for this item.
    lesson_specs = []
    for j, lesson in enumerate(source_lessons):
        field = f"步骤 {j + 1}"
        if not isinstance(lesson, dict):
            raise ValueError(f"{field}必须是 JSON 对象")
        ltitle = string_field(lesson.get("title"), f"{field} title", 160, required=True)
        if ("content" in lesson) == ("contentFile" in lesson):
            raise ValueError(f"{field}必须且只能提供 content 或 contentFile")
        body, content_file = None, None
        if "content" in lesson:
            value = lesson["content"]
            if not isinstance(value, str):
                raise ValueError(f"{field} content 必须是字符串")
            size = len(value.encode("utf-8"))
            if size > MAX_LESSON_BYTES:
                raise ValueError(f"{field}正文超过 1MB")
            body = value.strip()
            if not body:
                raise ValueError(f"{field}正文不能为空")
            text_budget.charge(size)
        else:
            content_file = normalize_relative_path(lesson["contentFile"], f"{field} contentFile")
        duration = lesson.get("duration", 10)
        if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 600:
            raise ValueError(f"{field} duration 必须是 1～600 的整数")
        preview = lesson.get("preview", j == 0)
        if not isinstance(preview, bool):
            raise ValueError(f"{field} preview 必须是 true/false")
        image_url, image_rel = image_source(lesson.get("image", ""), f"{field} image")
        text_budget.charge(len(ltitle.encode("utf-8")))
        lesson_specs.append((ltitle, body, content_file, duration, preview, image_url, image_rel))
    cover_url, cover_rel = image_source(raw.get("cover", ""), "cover")
    resource_rel = resource_source(raw.get("resource", ""))
    lessons = []
    for ltitle, body, content_file, duration, preview, image_url, image_rel in lesson_specs:
        if content_file is not None:
            body = resolver.text(content_file, "contentFile")
        lessons.append({"title": ltitle, "content": body, "duration": duration,
                        "preview": preview, "image_url": image_url,
                        "image_file": resolver.asset(image_rel, "image") if image_rel else None})
    cover_file = resolver.asset(cover_rel, "cover") if cover_rel else None
    resource_file = resolver.asset(resource_rel, "resource") if resource_rel else None
    for lesson in lessons:
        lesson["image_name"] = lesson["image_file"]["name"] if lesson["image_file"] else None
    return {"index": index, "title": title, "summary": summary, "description": description,
            "category": category, "level": level, "price": price, "tags": tags,
            "cover_url": cover_url, "cover_file": cover_file,
            "cover_name": cover_file["name"] if cover_file else None,
            "resource_file": resource_file,
            "resource_name": resource_file["name"] if resource_file else None,
            "lessons": lessons}


def item_error(index, raw, exc):
    title = raw.get("title") if isinstance(raw, dict) else None
    title = title.strip()[:120] if isinstance(title, str) else ""
    message = str(exc)
    return {"index": index, "title": title or f"第 {index + 1} 项", "status": "failed",
            "message": message, "error": message}
