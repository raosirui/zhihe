"""Historical document refresh stays inside isolated uploads and is reversible."""
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from extensions import db
from models import Lesson
from rebuild_lesson_previews import private_raw, rebuild, restore, unchanged_html


def _setup_rtf(app, lesson_id):
    with app.app_context():
        lesson = db.session.get(Lesson, lesson_id)
        directory = Path(app.config["UPLOAD_FOLDER"]) / "lesson_raw"
        directory.mkdir(exist_ok=True)
        source = directory / f"lesson_{lesson.id}_fixture.rtf"
        source.write_bytes(b"{\\rtf1\\ansi Hello \\b world\\b0\\par Second line}")
        lesson.raw_file_path = str(source)
        lesson.content_type = "richtext"
        lesson.converted_html = "<p>Prior preview</p>"
        db.session.commit()
        return source


def test_dry_run_and_reversible_apply_only_update_derivative(sqlite_app, sqlite_data, tmp_path):
    lesson_id = sqlite_data["lessons"]["freeFirst"]
    source = _setup_rtf(sqlite_app, lesson_id)
    original_raw = source.read_bytes()
    backup_dir = tmp_path / "private-backups"
    backup_dir.mkdir(mode=0o700)
    assert rebuild(sqlite_app, [lesson_id]) == 0
    with sqlite_app.app_context():
        assert db.session.get(Lesson, lesson_id).converted_html == "<p>Prior preview</p>"
    assert rebuild(sqlite_app, [lesson_id], apply=True, backup_dir=backup_dir) == 0
    backups = list(backup_dir.glob("*.json"))
    assert len(backups) == 1
    saved = json.loads(backups[0].read_text())
    assert saved["oldHtml"] == "<p>Prior preview</p>"
    with sqlite_app.app_context():
        lesson = db.session.get(Lesson, lesson_id)
        assert "Hello" in lesson.converted_html
        assert lesson.raw_file_path == str(source)
        assert lesson.content_type == "richtext"
        assert lesson.title == "Free first"
    assert source.read_bytes() == original_raw
    restore(sqlite_app, backups[0])
    with sqlite_app.app_context():
        assert db.session.get(Lesson, lesson_id).converted_html == "<p>Prior preview</p>"


def test_html_short_extension_is_eligible_for_rebuild(sqlite_app, sqlite_data):
    lesson_id = sqlite_data["lessons"]["freeFirst"]
    with sqlite_app.app_context():
        lesson = db.session.get(Lesson, lesson_id)
        directory = Path(sqlite_app.config["UPLOAD_FOLDER"]) / "lesson_raw"
        directory.mkdir(exist_ok=True)
        original = directory / f"lesson_{lesson_id}_short.htm"
        original.write_text("<h2>Safe heading</h2>", encoding="utf-8")
        lesson.raw_file_path = str(original)
        lesson.content_type = "html"
        lesson.converted_html = "<p>Old</p>"
        db.session.commit()
    assert rebuild(sqlite_app, [lesson_id]) == 0


def test_conditional_html_update_is_byte_exact_on_mysql(mysql_app, mysql_data):
    lesson_id = mysql_data["lessons"]["freeFirst"]
    with mysql_app.app_context():
        db.session.get(Lesson, lesson_id).converted_html = "Résumé"
        db.session.commit()
        assert db.session.execute(select(Lesson.id).where(
            Lesson.id == lesson_id, Lesson.converted_html == "resume")).first()
        assert db.session.execute(select(Lesson.id).where(
            Lesson.id == lesson_id, unchanged_html("resume"))).first() is None
        assert db.session.execute(select(Lesson.id).where(
            Lesson.id == lesson_id, unchanged_html("Résumé"))).first()


def test_rebuild_rejects_foreign_and_symlinked_originals(sqlite_app, sqlite_data, tmp_path):
    lesson_id = sqlite_data["lessons"]["freeFirst"]
    source = _setup_rtf(sqlite_app, lesson_id)
    with sqlite_app.app_context():
        lesson = db.session.get(Lesson, lesson_id)
        outside = tmp_path / "outside.rtf"
        outside.write_bytes(source.read_bytes())
        lesson.raw_file_path = str(outside)
        db.session.commit()
        with pytest.raises(ValueError, match="私有原件目录"):
            private_raw(sqlite_app, lesson)
        alias = source.with_name(f"lesson_{lesson_id}_alias.rtf")
        alias.symlink_to(source)
        lesson.raw_file_path = str(alias)
        db.session.commit()
        with pytest.raises(ValueError, match="绝对路径"):
            private_raw(sqlite_app, lesson)
