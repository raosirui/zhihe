"""Document conversion service for multi-format support."""
import os
import tempfile
from pathlib import Path

import mammoth
import pdfplumber
from pptx import Presentation
import markdown
import bleach
from werkzeug.utils import secure_filename


ALLOWED_EXTENSIONS = {
    "markdown": [".md", ".markdown", ".txt"],
    "word": [".docx"],
    "pdf": [".pdf"],
    "html": [".html", ".htm"],
    "richtext": [".rtf"],
    "ppt": [".pptx"],
}


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
        html = result.value
        # Sanitize HTML to prevent XSS
        html = bleach.clean(
            html,
            tags=bleach.ALLOWED_TAGS + ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                                         "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
                                         "img", "span", "sub", "sup", "br", "hr"],
            attributes=bleach.ALLOWED_ATTRIBUTES | {"img": ["src", "alt", "width", "height"]},
        )
        return html


def convert_pdf_to_html(file_path):
    """Convert PDF to HTML (extract text with structure)."""
    html_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text:
                # Convert text to HTML paragraphs
                paragraphs = text.split("\n\n")
                html_parts.append(f'<div class="pdf-page" data-page="{page_num}">')
                for para in paragraphs:
                    if para.strip():
                        escaped = bleach.clean(para, tags=[], strip=True)
                        html_parts.append(f"<p>{escaped}</p>")
                html_parts.append("</div>")
    return "\n".join(html_parts)


def convert_ppt_to_html(file_path):
    """Convert PowerPoint to HTML (extract slides as sections)."""
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
    return html


def convert_html_file(file_path):
    """Read and sanitize HTML file."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Sanitize HTML
    html = bleach.clean(
        content,
        tags=bleach.ALLOWED_TAGS + ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                                     "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
                                     "img", "span", "sub", "sup", "br", "hr", "a", "strong", "em"],
        attributes=bleach.ALLOWED_ATTRIBUTES | {
            "img": ["src", "alt", "width", "height"],
            "a": ["href", "title"],
        },
    )
    return html


def convert_document(file_path, content_type):
    """Convert document to HTML based on content type."""
    converters = {
        "markdown": convert_markdown_to_html,
        "word": convert_word_to_html,
        "pdf": convert_pdf_to_html,
        "ppt": convert_ppt_to_html,
        "html": convert_html_file,
        "richtext": convert_html_file,
    }
    converter = converters.get(content_type)
    if not converter:
        raise ValueError(f"Unsupported content type: {content_type}")
    return converter(file_path)


def save_raw_file(file_storage, upload_folder, lesson_id=None):
    """Save uploaded file to disk and return path."""
    filename = secure_filename(file_storage.filename)
    if lesson_id:
        filename = f"lesson_{lesson_id}_{filename}"
    file_path = os.path.join(upload_folder, "lesson_raw", filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    file_storage.save(file_path)
    return file_path
