"""Isolated document upload, preview, conversion and edit regressions."""
import base64
import io
import json
import zipfile
from pathlib import Path

from sqlalchemy import delete, select

import pytest
from docx import Document
from flask_jwt_extended import create_access_token
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Inches, Pt
from werkzeug.security import generate_password_hash

from app import db
from document_converter import convert_rtf_to_html, render_ppt_preview
from models import FileAsset, FileReference, Lesson, User


@pytest.fixture
def env(sqlite_app):
    with sqlite_app.app_context():
        users = [User(username=name, nickname=name, role=role, balance=100,
                      password_hash=generate_password_hash("testpassword"))
                 for name, role in [("author", "user"), ("buyer", "user"),
                                    ("stranger", "user"), ("admin", "admin")]]
        db.session.add_all(users)
        db.session.commit()
        headers = {user.username: {"Authorization": "Bearer " + create_access_token(
            identity=str(user.id), additional_claims={"token_version": user.token_version})} for user in users}
    return sqlite_app, sqlite_app.test_client(), headers


def sample_ppt():
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    paragraph = box.text_frame.paragraphs[0]
    paragraph.alignment = 2  # center
    run = paragraph.add_run()
    run.text = "Visible slide content"
    run.font.name = "Arial"
    run.font.size = Pt(24)
    run.font.bold = True
    run.font.color.rgb = RGBColor(11, 22, 33)
    rect = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1), Inches(2), Inches(2), Inches(1))
    rect.fill.solid()
    rect.fill.fore_color.rgb = RGBColor(42, 88, 123)
    slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(4), Inches(2), Inches(1), Inches(1))
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(3), Inches(3), Inches(1), Inches(4))
    line.line.color.rgb = RGBColor(3, 4, 5)
    table = slide.shapes.add_table(1, 2, Inches(1), Inches(4), Inches(5), Inches(0.5)).table
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    pixels = io.BytesIO()
    Image.new("RGB", (16, 12), "red").save(pixels, format="PNG")
    slide.shapes.add_picture(io.BytesIO(pixels.getvalue()), Inches(7), Inches(2))
    photo = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(photo, format="JPEG")
    slide.shapes.add_picture(io.BytesIO(photo.getvalue()), Inches(8), Inches(3))
    unsupported = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(unsupported, format="GIF")
    slide.shapes.add_picture(io.BytesIO(unsupported.getvalue()), Inches(9), Inches(4))
    stream = io.BytesIO()
    prs.save(stream)
    return stream.getvalue()


def upload_document(env, body, filename, *, price=10, status="published", two_lessons=False):
    _, client, headers = env
    lessons = [{"title": "open", "content": "", "preview": True}]
    if two_lessons:
        lessons.append({"title": "private", "content": "", "preview": False})
    data = {"title": "Document kit", "description": "description", "price": str(price),
            "status": status, "lessons": json.dumps(lessons)}
    for i in range(len(lessons)):
        data[f"lesson_file_{i}"] = (io.BytesIO(body), filename)
    response = client.post("/api/toolkits", data=data, headers=headers["author"])
    assert response.status_code == 201, response.json
    return response.json


def test_ppt_layout_contract_data_only_and_legacy_html(env):
    _, client, headers = env
    body = sample_ppt()
    toolkit = upload_document(env, body, "lesson.pptx")
    lesson = toolkit["lessons"][0]
    assert lesson["contentType"] == "ppt"
    assert "Visible slide content" in lesson["convertedHtml"]
    assert lesson["rawFileUrl"].startswith("/api/lesson-raw/")
    preview = client.get(f"/api/lesson-preview/{lesson['id']}")
    assert preview.status_code == 200, preview.json
    assert preview.headers["Cache-Control"] == "private, no-store"
    assert preview.mimetype == "application/json"
    assert preview.json["version"] == 1
    assert (preview.json["width"], preview.json["height"]) == (960, 480)
    shapes = preview.json["slides"][0]["shapes"]
    types = [shape["type"] for shape in shapes]
    assert types == ["text", "rect", "ellipse", "line", "table", "image", "image"]
    text = shapes[0]
    assert text["x"] == 96 and text["paragraphs"][0]["align"] == "center"
    assert text["paragraphs"][0]["runs"][0] == {
        "text": "Visible slide content", "bold": True, "italic": False, "underline": False,
        "fontSize": 32, "fontFamily": "Arial", "color": "#0B1621"}
    assert shapes[1]["fill"] == "#2A587B"
    assert shapes[3]["x1"] == 288 and shapes[3]["x2"] == 96
    assert shapes[3]["stroke"] == "#030405"
    assert shapes[4]["rows"][0][0]["paragraphs"][0]["runs"][0]["text"] == "A"
    for shape in shapes[-2:]:
        assert shape["src"].startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))
        assert base64.b64decode(shape["src"].split(",", 1)[1])
    assert "<" not in json.dumps(preview.json)  # Never return embedded HTML.
    assert client.get(lesson["rawFileUrl"], headers=headers["author"]).data == body


def test_preview_shares_raw_policy_across_preview_paid_draft_offline(env):
    _, client, headers = env
    toolkit = upload_document(env, sample_ppt(), "lesson.pptx", two_lessons=True)
    first, locked = (lesson["id"] for lesson in toolkit["lessons"])
    for prefix in ("lesson-preview", "lesson-raw"):
        assert client.get(f"/api/{prefix}/{first}").status_code == 200
        denied = client.get(f"/api/{prefix}/{locked}")
        assert denied.status_code == 403
        assert b"Visible slide content" not in denied.data
        assert client.get(f"/api/{prefix}/{locked}", headers=headers["author"]).status_code == 200
        assert client.get(f"/api/{prefix}/{locked}", headers=headers["admin"]).status_code == 200
        assert client.get(f"/api/{prefix}/{locked}", headers={"Authorization": "Bearer broken"}).status_code == 401
        assert client.get(f"/api/{prefix}/999999").status_code == 404
    toolkit_id = toolkit["id"]
    assert client.put(f"/api/toolkits/{toolkit_id}/status", json={"status": "offline"},
                      headers=headers["author"]).status_code == 200
    for prefix in ("lesson-preview", "lesson-raw"):
        assert client.get(f"/api/{prefix}/{first}").status_code == 403
        assert client.get(f"/api/{prefix}/{first}", headers=headers["author"]).status_code == 200
    draft = upload_document(env, sample_ppt(), "draft.pptx", status="draft")
    draft_id = draft["lessons"][0]["id"]
    for prefix in ("lesson-preview", "lesson-raw"):
        assert client.get(f"/api/{prefix}/{draft_id}").status_code == 403
        assert client.get(f"/api/{prefix}/{draft_id}", headers=headers["author"]).status_code == 200
    paid = upload_document(env, sample_ppt(), "paid.pptx", two_lessons=True)
    paid_locked = paid["lessons"][1]["id"]
    order = client.post("/api/orders", json={"toolkitId": paid["id"]}, headers=headers["buyer"])
    assert order.status_code == 201
    assert client.post(f"/api/orders/{order.json['id']}/pay", headers=headers["buyer"]).status_code == 200
    assert client.put(f"/api/toolkits/{paid['id']}/status", json={"status": "offline"},
                      headers=headers["author"]).status_code == 200
    for prefix in ("lesson-preview", "lesson-raw"):
        assert client.get(f"/api/{prefix}/{paid_locked}", headers=headers["buyer"]).status_code == 200
        assert client.get(f"/api/{prefix}/{paid_locked}", headers=headers["stranger"]).status_code == 403


def _raw_asset(app, lesson_id):
    with app.app_context():
        ref = db.session.execute(select(FileReference).where(
            FileReference.lesson_id == lesson_id, FileReference.usage == "lesson_raw")).scalar_one()
        asset = db.session.get(FileAsset, ref.asset_id)
        return Path(app.config["UPLOAD_FOLDER"]) / "lesson_raw" / asset.stored_name, asset.id


def test_raw_and_preview_paths_cannot_escape_storage_or_cross_lessons(env, tmp_path):
    app, client, headers = env
    kit = upload_document(env, sample_ppt(), "safe.pptx", two_lessons=True)
    first, second = (lesson["id"] for lesson in kit["lessons"])
    # Explicitly construct historical, unregistered originals: object-backed
    # originals are resolved by immutable lesson references, not arbitrary paths.
    with app.app_context():
        root = Path(app.config["UPLOAD_FOLDER"]) / "lesson_raw"
        root.mkdir(exist_ok=True)
        first_path = root / f"lesson_{first}_fixture.pptx"
        second_path = root / f"lesson_{second}_fixture.pptx"
        first_path.write_bytes(sample_ppt())
        second_path.write_bytes(sample_ppt())
        db.session.execute(delete(FileReference).where(
            FileReference.lesson_id.in_([first, second]), FileReference.usage == "lesson_raw"))
        db.session.get(Lesson, first).raw_file_path = str(first_path)
        db.session.get(Lesson, second).raw_file_path = str(second_path)
        db.session.commit()
    outside = tmp_path / "lesson_raw" / f"lesson_{first}_outside.pptx"
    outside.parent.mkdir()
    outside.write_bytes(sample_ppt())
    link = first_path.with_name(f"lesson_{first}_link.pptx")
    link.symlink_to(first_path)
    for bad in (str(outside), str(second_path), str(link)):
        with app.app_context():
            db.session.get(Lesson, first).raw_file_path = bad
            db.session.commit()
        for prefix in ("lesson-preview", "lesson-raw"):
            assert client.get(f"/api/{prefix}/{first}", headers=headers["author"]).status_code == 404
    with app.app_context():
        db.session.get(Lesson, first).raw_file_path = str(first_path)
        db.session.commit()
    assert client.get(f"/api/lesson-preview/{first}").status_code == 200
    assert client.get(f"/api/lesson-raw/{first}").data == first_path.read_bytes()
    link.unlink()


def test_historical_mislabeled_document_can_still_be_downloaded(env):
    app, client, _ = env
    kit = upload_document(env, sample_ppt(), "historical.pptx", price=0)
    lesson = kit["lessons"][0]
    with app.app_context():
        db.session.get(Lesson, lesson["id"]).content_type = "markdown"
        db.session.commit()
    raw = client.get(lesson["rawFileUrl"])
    assert raw.status_code == 200 and raw.data.startswith(b"PK")
    assert client.get(f"/api/lesson-preview/{lesson['id']}").status_code == 404


def test_metadata_edits_keep_file_type_html_path_and_reject_spoofing(env):
    app, client, headers = env
    kit = upload_document(env, sample_ppt(), "original.pptx")
    lesson = kit["lessons"][0]
    lid, tid = lesson["id"], kit["id"]
    with app.app_context():
        original_path = db.session.get(Lesson, lid).raw_file_path
    metadata = client.put(f"/api/toolkits/{tid}", json={"title": "Renamed"}, headers=headers["author"])
    assert metadata.status_code == 200, metadata.json
    assert metadata.json["lessons"][0]["contentType"] == "ppt"
    assert metadata.json["lessons"][0]["convertedHtml"] == lesson["convertedHtml"]
    payload = {"lessons": [{"id": lid, "title": "Step renamed", "content": lesson["content"],
                            "contentType": "ppt", "preview": True}]}
    assert client.put(f"/api/toolkits/{tid}", json=payload, headers=headers["author"]).status_code == 200
    payload["lessons"][0]["contentType"] = "html"
    denied = client.put(f"/api/toolkits/{tid}", json=payload, headers=headers["author"])
    assert denied.status_code == 400 and "文档类型" in denied.json["message"]
    payload["lessons"][0].pop("contentType")
    payload["lessons"][0]["content_type"] = "html"
    assert client.put(f"/api/toolkits/{tid}", json=payload, headers=headers["author"]).status_code == 400
    payload["lessons"][0]["contentType"] = "ppt"
    assert client.put(f"/api/toolkits/{tid}", json=payload, headers=headers["author"]).status_code == 400
    payload["lessons"][0].pop("content_type")
    payload["lessons"][0]["contentType"] = "html"
    replaced = client.put(f"/api/toolkits/{tid}", data={"lessons": json.dumps(payload["lessons"]),
        "lesson_file_0": (io.BytesIO(sample_ppt()), "replacement.pptx")}, headers=headers["author"])
    assert replaced.status_code == 400  # Declared type disagrees with the actual file.
    with app.app_context():
        stored = db.session.get(Lesson, lid)
        assert stored.content_type == "ppt" and stored.raw_file_path == original_path
        assert stored.converted_html == lesson["convertedHtml"]
    assert client.get(f"/api/lesson-preview/{lid}").status_code == 200
    matching = client.put(f"/api/toolkits/{tid}", data={"lessons": json.dumps(payload["lessons"]),
        "lesson_file_0": (io.BytesIO(b"<h2>Replacement</h2>"), "replacement.html")},
        headers=headers["author"])
    assert matching.status_code == 200, matching.json
    assert matching.json["lessons"][0]["contentType"] == "html"
    assert matching.json["lessons"][0]["convertedHtml"] == "<h2>Replacement</h2>"
    assert client.get(f"/api/lesson-preview/{lid}").status_code == 404


def test_failed_multi_file_upload_reclaims_all_uncommitted_originals(env):
    app, client, headers = env
    lessons = [{"title": "first", "content": ""}, {"title": "second", "content": ""}]
    response = client.post("/api/toolkits", data={
        "title": "Rejected documents", "description": "description", "price": "0",
        "lessons": json.dumps(lessons),
        "lesson_file_0": (io.BytesIO(sample_ppt()), "first.pptx"),
        "lesson_file_1": (io.BytesIO(b"not a presentation"), "second.pptx"),
    }, headers=headers["author"])
    assert response.status_code == 400
    assert not list((Path(app.config["UPLOAD_FOLDER"]) / "lesson_raw").iterdir())


def test_draft_toolkit_deletion_reclaims_private_originals(env):
    app, client, headers = env
    kit = upload_document(env, sample_ppt(), "private.pptx", price=0, status="draft")
    lesson_id = kit["lessons"][0]["id"]
    original, asset_id = _raw_asset(app, lesson_id)
    assert original.exists()
    response = client.delete(f"/api/toolkits/{kit['id']}", headers=headers["author"])
    assert response.status_code == 200 and response.json["mode"] == "deleted"
    assert not original.exists()
    with app.app_context():
        assert db.session.get(FileAsset, asset_id).state == "deleted"


def test_title_only_partial_edit_keeps_markdown_body(env):
    _, client, headers = env
    kit = upload_document(env, b"# Original markdown\n\nDetailed body", "source.md", price=0)
    lesson = kit["lessons"][0]
    response = client.put(f"/api/toolkits/{kit['id']}", json={
        "lessons": [{"id": lesson["id"], "title": "New title"}],
    }, headers=headers["author"])
    assert response.status_code == 200, response.json
    assert response.json["lessons"][0]["content"] == lesson["content"]
    assert response.json["lessons"][0]["contentType"] == "markdown"
    assert client.get(lesson["rawFileUrl"]).status_code == 200


def test_unicode_filename_keeps_extension_and_replacement_reclaims_old_original(env):
    app, client, headers = env
    kit = upload_document(env, sample_ppt(), "教材.pptx", price=0)
    lesson = kit["lessons"][0]
    lesson_id = lesson["id"]
    original, old_id = _raw_asset(app, lesson_id)
    assert original.suffix == ".pptx" and original.exists()
    assert client.get(lesson["rawFileUrl"]).status_code == 200
    assert client.get(f"/api/lesson-preview/{lesson_id}").status_code == 200
    response = client.put(f"/api/toolkits/{kit['id']}", data={
        "lessons": json.dumps([{"id": lesson_id, "title": "replaced", "content": "",
                               "contentType": "ppt"}]),
        "lesson_file_0": (io.BytesIO(sample_ppt()), "第二版.pptx"),
    }, headers=headers["author"])
    assert response.status_code == 200, response.json
    replacement, new_id = _raw_asset(app, lesson_id)
    assert new_id != old_id and replacement != original and replacement.exists()
    assert not original.exists()
    assert client.get(f"/api/lesson-preview/{lesson_id}").status_code == 200


def _docx_bytes():
    word = Document()
    word.add_heading("Word heading", level=1)
    word.add_paragraph("Word body text")
    stream = io.BytesIO()
    word.save(stream)
    return stream.getvalue()


def _pdf_bytes():
    """A tiny PDF containing a visible text operator, with explicit xref offsets."""
    content = b"BT /F1 12 Tf 20 90 Td (PDF body text) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 120] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for index, part in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(index).encode() + b" 0 obj\n" + part + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return data + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"


@pytest.mark.parametrize("name,body,expected", [
    ("source.md", b"# Markdown heading\n\n**bold**", "<h1>Markdown heading</h1>"),
    ("source.html", b"<html><head><title>SECRET TITLE</title><meta charset='utf-8'><style>SECRET CSS</style></head>"
                    b"<body><h2>HTML heading</h2><script>SECRET SCRIPT</script><a href='javascript:alert(1)'>Link</a>"
                    b"<a href='https://example.org'>Safe</a></body></html>", "<h2>HTML heading</h2>"),
    ("source.rtf", rb"{\rtf1\ansi Plain \b bold\b0  rest \i italic\i0  \ul underline\ulnone \par " + bytes((92,)) + b"u20013?}", "<strong>bold</strong>"),
    ("source.docx", _docx_bytes, "Word heading"),
    ("source.pdf", _pdf_bytes, "PDF body text"),
])
def test_five_other_formats_keep_sanitized_html_and_no_ppt_endpoint(env, name, body, expected):
    _, client, _ = env
    data = body() if callable(body) else body
    kit = upload_document(env, data, name, price=0)
    lesson = kit["lessons"][0]
    html = lesson["convertedHtml"]
    assert expected in html, (name, html)
    assert "<script" not in html.lower() and "javascript:" not in html.lower()
    if name.endswith(".html"):
        assert not any(secret in html for secret in ("SECRET TITLE", "SECRET CSS", "SECRET SCRIPT"))
        assert 'href="https://example.org"' in html
    if name.endswith(".rtf"):
        assert all(tag in html for tag in ("<strong>", "<em>", "<u>", "中"))
    assert client.get(f"/api/lesson-preview/{lesson['id']}").status_code == 404
    assert client.get(lesson["rawFileUrl"]).data == data


def test_rtf_striprtf_fallback_and_html_text_removed(tmp_path, monkeypatch):
    import document_converter
    rtf = tmp_path / "text.rtf"
    rtf.write_bytes(rb"{\rtf1\ansi Fallback \b text\b0  and " + bytes((92,)) + b"u20013?}")
    monkeypatch.setattr(document_converter, "_rtf_inline", lambda _: (_ for _ in ()).throw(ValueError("unsupported")))
    output = convert_rtf_to_html(rtf)
    assert "Fallback" in output and "text" in output and "中" in output
    invalid = tmp_path / "invalid.rtf"
    invalid.write_text("<script>alert(1)</script>")
    with pytest.raises(ValueError, match="无效"):
        convert_rtf_to_html(invalid)
    html = tmp_path / "source.html"
    html.write_text("<style>bad</style><title>private</title><p>Shown &amp; safe</p><script>evil</script>")
    output = document_converter.convert_html_file(html)
    assert output == "<p>Shown &amp; safe</p>"
    legacy = tmp_path / "legacy.rtf"
    legacy.write_bytes(rb"{\rtf1\ansi\ansicpg936 \'d6\'d0}")
    assert "中" in convert_rtf_to_html(legacy)
    oversized = tmp_path / "oversized.rtf"
    oversized.write_bytes(b"{\\rtf1\\ansi " + b"x" * 500_001 + b"}")
    with pytest.raises(ValueError, match="过大"):
        convert_rtf_to_html(oversized)


def test_ppt_slide_budget_is_enforced_and_converted_html_survives(env):
    _, client, _ = env
    prs = Presentation()
    for _ in range(41):
        prs.slides.add_slide(prs.slide_layouts[6])
    ppt = io.BytesIO()
    prs.save(ppt)
    kit = upload_document(env, ppt.getvalue(), "large.pptx")
    lesson = kit["lessons"][0]
    assert "Slide 41" in lesson["convertedHtml"]
    preview = client.get(f"/api/lesson-preview/{lesson['id']}")
    assert preview.status_code == 422
    assert preview.headers["Cache-Control"] == "private, no-store"
    assert "Slide 41" not in preview.get_data(as_text=True)
    assert lesson["rawFileUrl"]


def test_ppt_zip_entries_are_bounded_before_parsing(tmp_path, monkeypatch):
    import document_converter
    flood = tmp_path / "many-parts.pptx"
    with zipfile.ZipFile(flood, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(1001):
            archive.writestr(f"part-{index}.xml", "x")
    monkeypatch.setattr(document_converter, "Presentation", lambda _: (_ for _ in ()).throw(AssertionError("parsed zip")))
    with pytest.raises(ValueError, match="解压内容过大"):
        render_ppt_preview(flood)


def test_ppt_does_not_embed_animated_png(tmp_path):
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(image, format="PNG", save_all=True,
                                            append_images=[Image.new("RGB", (8, 8), "blue")])
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_picture(io.BytesIO(image.getvalue()), Inches(1), Inches(1))
    source = tmp_path / "animated.pptx"
    prs.save(source)
    assert not render_ppt_preview(source)["slides"][0]["shapes"]


def test_ppt_user_text_is_data_not_html(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = '<img src=x onerror="alert(1)">'
    file_path = tmp_path / "literal.pptx"
    prs.save(file_path)
    result = render_ppt_preview(file_path)
    assert result["slides"][0]["shapes"][0]["paragraphs"][0]["runs"][0]["text"] == '<img src=x onerror="alert(1)">'
    assert all(shape["type"] == "text" for shape in result["slides"][0]["shapes"])
