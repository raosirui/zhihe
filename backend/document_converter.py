"""Document conversion and bounded, data-only PowerPoint previews."""
import base64
import io
import os
import re
import uuid
import zipfile
from html import escape
from html.parser import HTMLParser
from pathlib import Path

import bleach
import mammoth
import markdown
import pdfplumber
from PIL import Image, UnidentifiedImageError
from pptx import Presentation
from pptx.enum.dml import MSO_COLOR_TYPE, MSO_FILL
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from striprtf.striprtf import rtf_to_text
from werkzeug.utils import secure_filename
from validation import DOCUMENT_EXTENSIONS


HTML_TAGS = bleach.ALLOWED_TAGS.union({
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead",
    "tbody", "tr", "th", "td", "img", "span", "sub", "sup", "br", "hr",
    "pre", "code", "blockquote", "u",
})


def allowed_attribute(tag, name, value):
    if tag == "a" and name == "href":
        return value.startswith(("https://", "http://", "mailto:", "#"))
    if tag == "a" and name == "title":
        return True
    if tag == "img" and name == "src":
        return value.startswith("https://") or value.startswith((
            "data:image/png;base64,", "data:image/jpeg;base64,",
            "data:image/gif;base64,", "data:image/webp;base64,",
        ))
    return tag == "img" and name in {"alt", "width", "height"}


def sanitize_html(content):
    return bleach.clean(content, tags=HTML_TAGS, attributes=allowed_attribute,
                        protocols=bleach.ALLOWED_PROTOCOLS | {"data"}, strip=True)


ALLOWED_EXTENSIONS = DOCUMENT_EXTENSIONS


def detect_content_type(filename):
    """Detect content type from filename extension."""
    ext = Path(filename).suffix.lower()
    for content_type, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return content_type
    return None


def is_allowed_extension(filename, content_type):
    """Check if filename has allowed extension for the content type."""
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS.get(content_type, [])


def convert_word_to_html(file_path):
    """Convert Word document (.docx) to HTML."""
    with open(file_path, "rb") as docx_file:
        result = mammoth.convert_to_html(docx_file)
        return sanitize_html(result.value)


def convert_pdf_to_html(file_path):
    """Convert PDF to HTML (extract text with structure)."""
    html_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text:
                paragraphs = text.split("\n\n")
                html_parts.append(f'<div class="pdf-page" data-page="{page_num}">')
                for para in paragraphs:
                    if para.strip():
                        escaped = bleach.clean(para, tags=[], strip=True)
                        html_parts.append(f"<p>{escaped}</p>")
                html_parts.append("</div>")
    return "\n".join(html_parts)


def convert_ppt_to_html(file_path):
    """Existing text-only fallback for browsers without structured PPT support."""
    _ppt_archive_guard(file_path)
    prs = Presentation(file_path)
    html_parts = []
    for slide_num, slide in enumerate(prs.slides, 1):
        html_parts.append(f'<div class="ppt-slide" data-slide="{slide_num}">')
        html_parts.append(f'<h3>Slide {slide_num}</h3>')
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                text = bleach.clean(shape.text, tags=[], strip=True)
                html_parts.append(f"<p>{text}</p>")
        html_parts.append("</div>")
    return "\n".join(html_parts)


def convert_markdown_to_html(file_path):
    """Convert Markdown to HTML."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    html = markdown.markdown(content, extensions=["tables", "fenced_code"])
    return sanitize_html(html)


class _BodyOnlyHTML(HTMLParser):
    """Discard inactive document elements *and their contents* before Bleach.

    Bleach strips unsafe markup, not text inside <head>/<script>/<style>. This
    small pre-pass prevents source/CSS/title text from appearing in the lesson.
    Its output is still always passed through the shared Bleach sanitizer.
    """
    OMIT = {"head", "title", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skipped = []

    def handle_starttag(self, tag, attrs):
        if tag in self.OMIT:
            if len(self.skipped) >= 64:
                raise ValueError("HTML 结构嵌套过深")
            self.skipped.append(tag)
        elif not self.skipped:
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if not self.skipped and tag not in self.OMIT:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.skipped:
            if tag in self.skipped:
                del self.skipped[self.skipped.index(tag):]
        else:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skipped:
            self.parts.append(escape(data, quote=False))

    def handle_entityref(self, name):
        if not self.skipped:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skipped:
            self.parts.append(f"&#{name};")


def convert_html_file(file_path):
    """Remove page chrome/active content, then sanitize remaining body markup."""
    with open(file_path, "rb") as source:
        data = source.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("HTML 文档过大")
    parser = _BodyOnlyHTML()
    parser.feed(data.decode("utf-8-sig"))
    parser.close()
    return sanitize_html("".join(parser.parts))


_RTF_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer",
    "headerl", "headerr", "footerl", "footerr", "listtable", "listoverridetable",
    "revtbl", "filetbl", "xmlnstbl", "datastore", "nonshppict", "shppict",
    "themedata", "generator", "fldinst", "annotation", "atnauthor",
}


def _rtf_inline(content):
    """Parse the common RTF character styles without ever emitting raw RTF/HTML.

    Other RTF features use the established striprtf plaintext fallback. Groups
    restore formatting, while metadata/picture/hidden destinations are ignored.
    """
    paragraphs = [[]]
    # Group state: (bold, italic, underline, uc, hidden, starred).
    state = (False, False, False, 1, False, False)
    stack = []
    index, remaining_fallback, output_size = 0, 0, 0

    def add(value):
        nonlocal output_size
        output_size += len(value)
        if output_size > 500_000:
            raise ValueError("RTF 文档内容过大")
        style = state[:3]
        runs = paragraphs[-1]
        if runs and runs[-1][1] == style:
            runs[-1][0].append(value)
        else:
            runs.append(([value], style))

    # Keep fallback length before slicing; this avoids treating \uN?'s '?' as text.
    def literal(value):
        nonlocal remaining_fallback
        if state[4]:
            return
        consumed = min(len(value), remaining_fallback)
        remaining_fallback -= consumed
        value = value[consumed:]
        if not value:
            return
        add(value)

    while index < len(content):
        char = content[index]
        if char == "{":
            if len(stack) >= 64:
                raise ValueError("RTF 嵌套层数过多")
            stack.append(state)
            index += 1
        elif char == "}":
            if not stack:
                raise ValueError("RTF 结构无效")
            state = stack.pop()
            index += 1
        elif char != "\\":
            if char not in "\r\n":
                literal(char)
            index += 1
        else:
            index += 1
            if index == len(content):
                raise ValueError("RTF 结构无效")
            token = content[index]
            if token in "{}\\":
                literal(token)
                index += 1
                continue
            if token == "'":
                if index + 2 >= len(content):
                    raise ValueError("RTF 字符编码无效")
                decoded = bytes.fromhex(content[index + 1:index + 3]).decode("cp1252")
                literal(decoded)
                index += 3
                continue
            if not token.isalpha() or not token.isascii():
                if token == "*":
                    state = (*state[:4], True, True)
                elif token == "~":
                    literal(" ")
                elif token == "-":
                    literal("­")
                elif token == "_":
                    literal("‑")
                index += 1
                continue
            start = index
            while index < len(content) and content[index].isascii() and content[index].isalpha():
                index += 1
            word = content[start:index].lower()
            start_number = index
            if index < len(content) and content[index] in "+-":
                index += 1
            while index < len(content) and content[index].isascii() and content[index].isdigit():
                index += 1
            number = int(content[start_number:index]) if content[start_number:index].lstrip("+-").isdigit() else None
            if index < len(content) and content[index] == " ":
                index += 1
            if word == "bin" and number is not None:
                index += min(number, len(content) - index) if number >= 0 else 0
            elif word in _RTF_DESTINATIONS or state[5]:
                state = (*state[:4], True, state[5])
            elif state[4]:
                continue
            elif word in {"b", "i", "ul", "ulnone", "plain"}:
                bold, italic, underline = state[:3]
                if word == "plain":
                    bold = italic = underline = False
                elif word == "b":
                    bold = number != 0
                elif word == "i":
                    italic = number != 0
                else:
                    underline = word != "ulnone" and number != 0
                state = (bold, italic, underline, *state[3:])
            elif word == "uc" and number is not None:
                state = (*state[:3], min(32, max(0, number)), *state[4:])
            elif word == "u" and number is not None:
                codepoint = number & 0xFFFF
                if 0xD800 <= codepoint <= 0xDFFF:
                    raise ValueError("RTF Unicode 字符无效")
                literal(chr(codepoint))
                remaining_fallback = state[3]
            elif word in {"par", "row"}:
                if len(paragraphs) > 20_000:
                    raise ValueError("RTF 段落过多")
                paragraphs.append([])
            elif word == "line":
                literal("\n")
            elif word == "tab":
                literal("\t")
            elif word in {"emdash", "endash", "bullet", "lquote", "rquote", "ldblquote", "rdblquote"}:
                literal({"emdash": "—", "endash": "–", "bullet": "•", "lquote": "‘",
                         "rquote": "’", "ldblquote": "“", "rdblquote": "”"}[word])
    if stack:
        raise ValueError("RTF 结构无效")
    parts = []
    for runs in paragraphs:
        if not runs or not any("".join(chunks).strip() for chunks, _ in runs):
            continue
        parts.append("<p>")
        for chunks, (bold, italic, underline) in runs:
            prefix = ("<strong>" if bold else "") + ("<em>" if italic else "") + ("<u>" if underline else "")
            suffix = ("</u>" if underline else "") + ("</em>" if italic else "") + ("</strong>" if bold else "")
            parts.extend((prefix, escape("".join(chunks)).replace("\n", "<br>"), suffix))
        parts.append("</p>")
    return "".join(parts)


def convert_rtf_to_html(file_path):
    """Render basic RTF inline emphasis; fall back to striprtf for other syntax."""
    with open(file_path, "rb") as source:
        data = source.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("RTF 文档过大")
    content = data.decode("latin-1")
    if not content.lstrip().startswith("{\\rtf"):
        raise ValueError("无效的 RTF 文件")
    codepage = re.search(r"\\ansicpg(\d+)", content[:1024], re.IGNORECASE)
    try:
        # The simple inline parser decodes single-byte cp1252 escapes. For
        # multibyte legacy encodings, striprtf preserves text more reliably.
        if codepage and codepage.group(1) != "1252":
            raise UnicodeError("RTF uses a legacy code page")
        result = _rtf_inline(content)
    except (ValueError, UnicodeError):
        text = rtf_to_text(content)
        if len(text) > 500_000 or len(text.splitlines()) > 20_000:
            raise ValueError("RTF 文档内容过大")
        result = "\n".join(f"<p>{escape(line)}</p>" for line in text.splitlines() if line.strip())
    return sanitize_html(result)


# A PPTX is a ZIP archive; bound both compressed and expanded data before
# python-pptx parses it. Rendering below also bounds slides, shapes and media.
_PPT_FILE_LIMIT = 16 * 1024 * 1024
_PPT_EXPANDED_LIMIT = 64 * 1024 * 1024
_PPT_IMAGE_LIMIT = 1 * 1024 * 1024
_PPT_MEDIA_LIMIT = 4 * 1024 * 1024
_PPT_TEXT_LIMIT = 80_000
_EMU_PER_PX = 9525


def _ppt_archive_guard(path):
    if Path(path).stat().st_size > _PPT_FILE_LIMIT:
        raise ValueError("演示文稿文件过大")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 1000 or sum(part.file_size for part in infos) > _PPT_EXPANDED_LIMIT:
            raise ValueError("演示文稿解压内容过大")
        if any(part.file_size > 4 * 1024 * 1024 for part in infos if part.filename.lower().endswith((".xml", ".rels"))):
            raise ValueError("演示文稿页面过大")


def _px(emu):
    return round(emu / _EMU_PER_PX, 2)


def _color(color):
    if color is not None and color.type == MSO_COLOR_TYPE.RGB and color.rgb is not None:
        return "#" + str(color.rgb).upper()
    return None


def _appearance(shape):
    result = {}
    fill = getattr(shape, "fill", None)
    if fill is not None and fill.type == MSO_FILL.SOLID:
        color = _color(fill.fore_color)
        if color:
            result["fill"] = color
    line = getattr(shape, "line", None)
    if line is not None and line.fill.type == MSO_FILL.SOLID:
        color = _color(line.color)
        if color:
            result["stroke"] = color
            if line.width:
                result["strokeWidth"] = min(40, _px(line.width))
    return result


class _PPTBudget:
    def __init__(self):
        self.shapes = 0
        self.paragraphs = 0
        self.runs = 0
        self.text = 0
        self.media = 0

    def count(self, field, amount, limit):
        value = getattr(self, field) + amount
        if value > limit:
            raise ValueError("演示文稿预览内容过大")
        setattr(self, field, value)


def _paragraphs(text_frame, budget):
    paragraphs = []
    aligns = {PP_ALIGN.LEFT: "left", PP_ALIGN.CENTER: "center", PP_ALIGN.RIGHT: "right", PP_ALIGN.JUSTIFY: "justify"}
    for paragraph in text_frame.paragraphs:
        budget.count("paragraphs", 1, 2000)
        runs = []
        for run in paragraph.runs:
            budget.count("runs", 1, 4000)
            text = run.text or ""
            budget.count("text", len(text), _PPT_TEXT_LIMIT)
            font = run.font
            data = {"text": text, "bold": bool(font.bold), "italic": bool(font.italic),
                    "underline": bool(font.underline)}
            if font.size:
                data["fontSize"] = min(160, max(6, round(font.size.pt * 4 / 3, 2)))
            if font.name:
                data["fontFamily"] = font.name[:100]
            color = _color(font.color)
            if color:
                data["color"] = color
            runs.append(data)
        paragraphs.append({"align": aligns.get(paragraph.alignment, "left"), "runs": runs})
    return paragraphs


def _image_source(image, budget):
    blob = image.blob
    if len(blob) > _PPT_IMAGE_LIMIT:
        return None
    try:
        with Image.open(io.BytesIO(blob)) as check:
            if (check.format not in {"PNG", "JPEG"} or getattr(check, "n_frames", 1) != 1 or
                    max(check.size) > 4096 or check.width * check.height > 16_000_000):
                return None
            if check.format == "PNG" and not blob.startswith(b"\x89PNG\r\n\x1a\n"):
                return None
            if check.format == "JPEG" and not blob.startswith(b"\xff\xd8\xff"):
                return None
            mime = "png" if check.format == "PNG" else "jpeg"
            check.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    budget.count("media", len(blob), _PPT_MEDIA_LIMIT)
    return f"data:image/{mime};base64,{base64.b64encode(blob).decode('ascii')}"


def _render_shape(shape, budget):
    budget.count("shapes", 1, 500)
    bounds = {"x": _px(shape.left), "y": _px(shape.top),
              "width": _px(shape.width), "height": _px(shape.height)}
    if any(abs(value) > 20_000 for value in bounds.values()):
        raise ValueError("演示文稿图形尺寸过大")
    kind = shape.shape_type
    if kind == MSO_SHAPE_TYPE.PICTURE:
        src = _image_source(shape.image, budget)
        return {"type": "image", **bounds, "src": src} if src else None
    if shape.has_table:
        table = shape.table
        if len(table.rows) * len(table.columns) > 400:
            raise ValueError("演示文稿表格过大")
        rows = []
        for row in table.rows:
            cells = []
            for cell in row.cells:
                fill = _color(cell.fill.fore_color) if cell.fill.type == MSO_FILL.SOLID else None
                entry = {"paragraphs": _paragraphs(cell.text_frame, budget)}
                if fill:
                    entry["fill"] = fill
                cells.append(entry)
            rows.append(cells)
        col_widths = [_px(column.width) for column in table.columns]
        row_heights = [_px(row.height) for row in table.rows]
        if any(not 0 <= size <= 20_000 for size in (*col_widths, *row_heights)):
            raise ValueError("演示文稿表格尺寸过大")
        return {"type": "table", **bounds, "colWidths": col_widths,
                "rowHeights": row_heights, "rows": rows}
    if kind == MSO_SHAPE_TYPE.LINE:
        appearance = _appearance(shape)
        return {"type": "line", **bounds, "x1": _px(shape.begin_x), "y1": _px(shape.begin_y),
                "x2": _px(shape.end_x), "y2": _px(shape.end_y),
                "stroke": appearance.get("stroke", "#000000"),
                "strokeWidth": appearance.get("strokeWidth", 1)}
    appearance = _appearance(shape)
    if shape.has_text_frame and shape.text.strip():
        return {"type": "text", **bounds, **appearance, "paragraphs": _paragraphs(shape.text_frame, budget)}
    if kind == MSO_SHAPE_TYPE.AUTO_SHAPE:
        primitive = "ellipse" if shape.auto_shape_type == MSO_AUTO_SHAPE_TYPE.OVAL else "rect"
        return {"type": primitive, **bounds, **appearance}
    return None


def render_ppt_preview(file_path):
    """Return bounded slide geometry/styles as JSON-safe primitives, never HTML.

    Intentionally approximates unsupported effects, grouped/freeform geometry and
    theme-derived colors; converted_html remains the text-only fallback.
    """
    _ppt_archive_guard(file_path)
    prs = Presentation(file_path)
    if len(prs.slides) > 40:
        raise ValueError("演示文稿页数不能超过 40 页")
    width, height = _px(prs.slide_width), _px(prs.slide_height)
    if not 1 <= width <= 4000 or not 1 <= height <= 4000:
        raise ValueError("演示文稿画布尺寸无效")
    budget = _PPTBudget()
    slides = []
    for slide in prs.slides:
        shapes = []
        for shape in slide.shapes:
            item = _render_shape(shape, budget)
            if item:
                shapes.append(item)
        slides.append({"shapes": shapes})
    return {"version": 1, "width": width, "height": height, "slides": slides}


def convert_document(file_path, content_type):
    """Convert document to HTML based on content type."""
    converters = {
        "markdown": convert_markdown_to_html,
        "word": convert_word_to_html,
        "pdf": convert_pdf_to_html,
        "ppt": convert_ppt_to_html,
        "html": convert_html_file,
        "richtext": convert_rtf_to_html,
    }
    converter = converters.get(content_type)
    if not converter:
        raise ValueError(f"Unsupported content type: {content_type}")
    return converter(file_path)


def save_raw_file(file_storage, upload_folder, lesson_id=None):
    """Save uploaded file, preserving the validated suffix of non-ASCII names."""
    original = file_storage.filename
    if not detect_content_type(original):
        raise ValueError("文档文件名无效")
    suffix = Path(original).suffix.lower()
    stem = secure_filename(Path(original).stem)[:80] or "document"
    filename = f"lesson_{lesson_id}_{uuid.uuid4().hex}_{stem}{suffix}"
    file_path = os.path.join(upload_folder, "lesson_raw", filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    try:
        file_storage.save(file_path)
    except Exception:
        Path(file_path).unlink(missing_ok=True)
        raise
    return file_path
