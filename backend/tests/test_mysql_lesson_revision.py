"""Lesson deletion/preview changes win over an RR completion read snapshot."""
import json
import re
import threading

import pytest
from sqlalchemy import event

from test_fixtures import login_headers
from test_mysql_regression import request

pytestmark = pytest.mark.mysql


@pytest.mark.parametrize("change,expected", [("delete", 404), ("revoke-preview", 403)])
def test_completion_rechecks_current_lesson_after_toolkit_lock(mysql_app, mysql_data, change, expected):
    from extensions import db
    from models import LessonProgress
    with mysql_app.test_client() as client:
        student = login_headers(client, mysql_data["users"]["legacy"])
        author = login_headers(client, mysql_data["users"]["author"])
    lesson_id = mysql_data["lessons"]["paidPreview"]
    toolkit_id = mysql_data["toolkits"]["paid"]
    read_window, release = threading.Event(), threading.Event()
    outcomes = []
    with mysql_app.app_context():
        engine = db.engine

    def observed(_connection, _cursor, statement, *_args):
        if (threading.current_thread().name == "qa-complete-revision" and
                re.search(r"SELECT\s+`?lesson`?\.`?toolkit_id`?", statement, re.I)):
            read_window.set()
            assert release.wait(15), "Author revision did not release the owned completion worker"

    event.listen(engine, "after_cursor_execute", observed)
    worker = threading.Thread(name="qa-complete-revision", target=lambda: outcomes.append(
        request(mysql_app, "POST", f"/api/lessons/{lesson_id}/complete", student)))
    worker.start()
    try:
        assert read_window.wait(5), "Completion did not establish its pre-lock read snapshot"
        lessons = [] if change == "delete" else [{
            "id": lesson_id, "title": "Paid preview", "content": "Preview withdrawn after the read",
            "duration": 5, "preview": False, "image": f"/api/lesson-asset/{mysql_data['assets']['preview']}"
        }]
        with mysql_app.test_client() as client:
            revision = client.put(f"/api/toolkits/{toolkit_id}", headers=author,
                                  data={"title": "QA current lesson revision", "description": "QA revision", "price": "100",
                                        "lessons": json.dumps(lessons)})
            assert revision.status_code == 200, revision.get_json()
    finally:
        release.set()
        worker.join(timeout=20)
        event.remove(engine, "after_cursor_execute", observed)
    assert not worker.is_alive()
    assert outcomes and outcomes[0][0] == expected, outcomes
    with mysql_app.app_context():
        assert LessonProgress.query.count() == 0
