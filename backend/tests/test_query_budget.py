"""List output stays bounded in query count and never loads lesson bodies."""
from sqlalchemy import event

from test_fixtures import login_headers


def test_list_serialization_has_constant_query_budget(sqlite_app, sqlite_data):
    from extensions import db
    from models import Lesson, Toolkit
    with sqlite_app.app_context():
        author = sqlite_data["users"]["author"]["id"]
        for index in range(24):
            toolkit = Toolkit(title=f"Query budget {index}", slug=f"query-budget-{index}", summary="QA", description="D" * 16000,
                              author_id=author, category="生信分析", level="入门", price=100, status="published")
            db.session.add(toolkit)
            db.session.flush()
            db.session.add(Lesson(toolkit_id=toolkit.id, title="Bounded body", content="C" * 64000,
                                  duration=5, sort_order=0, is_preview=True))
        db.session.commit()
        engine = db.engine
    with sqlite_app.test_client() as client:
        auth = login_headers(client, sqlite_data["users"]["buyer"])
    statements = []

    def observed(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", observed)
    try:
        with sqlite_app.test_client() as client:
            response = client.get("/api/toolkits?per_page=50&page=1", headers=auth)
            assert response.status_code == 200
            items = response.get_json()["items"]
            assert len(items) >= 24 and all("lessons" not in item for item in items)
            assert len(response.data) < 100000
    finally:
        event.remove(engine, "before_cursor_execute", observed)
    assert len(statements) <= 12, f"List caused {len(statements)} SELECTs for {len(items)} items"
    assert not any("lesson.content" in statement or "lesson_content" in statement for statement in statements)
