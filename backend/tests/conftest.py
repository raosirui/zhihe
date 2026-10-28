"""Fixtures never load .env, target the business database, or reuse demo IDs."""
from pathlib import Path
import sys

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from test_support import IsolatedMySQL, isolated_config


@pytest.fixture(scope="session")
def mysql_instance():
    with IsolatedMySQL() as instance:
        yield instance


@pytest.fixture
def mysql_app(tmp_path, mysql_instance):
    from app import create_app
    from extensions import db
    mysql_instance.assert_owned()
    application = create_app(isolated_config(mysql_instance.uri, tmp_path / "uploads"))
    with application.app_context():
        # Only ORM tables in the verified owned schema; the isolation marker stays.
        db.drop_all()
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    mysql_instance.assert_owned()


@pytest.fixture
def sqlite_app(tmp_path):
    from app import create_app
    from extensions import db
    application = create_app(isolated_config("sqlite:///:memory:", tmp_path / "uploads"))
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def mysql_data(mysql_app, mysql_instance):
    from test_fixtures import build_dataset
    with mysql_app.app_context():
        return build_dataset(mysql_app)


@pytest.fixture
def sqlite_data(sqlite_app):
    from test_fixtures import build_dataset
    with sqlite_app.app_context():
        return build_dataset(sqlite_app)
