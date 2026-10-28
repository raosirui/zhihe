"""Factory/build-only serving and foreground launch regression tests."""
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock

from flask import Flask, jsonify
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException

from app import create_app
from config import build_config
from static_spa import install_spa
import wsgi

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def build_fixture(tmp_path):
    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Synthetic build fixture</title>")
    (dist / "assets" / "fixture-a1b2.js").write_text("console.log('synthetic build');")
    (dist.parent / "src").mkdir()
    (dist.parent / "src" / "secret.ts").write_text("synthetic source must not be served")
    application = Flask("static-fixture", static_folder=None)

    @application.errorhandler(HTTPException)
    def errors(error):
        return jsonify({"message": "fixture not found"}), error.code

    @application.get("/api/health")
    def health():
        return jsonify({"ok": True})

    install_spa(application, dist)
    return application, dist


def production_config(tmp_path, **extra):
    return {"APP_ENV": "production", "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(tmp_path / "uploads"), "JWT_SECRET_KEY": "fixture-strong-production-secret-" + "x" * 40,
            "WALLET_MODE": "disabled", "DEMO_SEED": False, "DEBUG": False, **extra}


def test_built_spa_and_api_404_json_are_same_origin(build_fixture):
    application, _dist = build_fixture
    client = application.test_client()
    assert b"Synthetic build fixture" in client.get("/").data
    assert b"Synthetic build fixture" in client.get("/learn/701/801").data
    assert client.get("/api/health").json == {"ok": True}
    for route in ["/api", "/api/", "/api/nonexistent", "/api/nonexistent/nested"]:
        response = client.get(route)
        assert response.status_code == 404 and response.is_json
        assert b"Synthetic build fixture" not in response.data
    asset = client.get("/assets/fixture-a1b2.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["Cache-Control"]
    assert client.get("/assets/missing.js").status_code == 404


@pytest.mark.parametrize("path", ["/../src/secret.ts", "/%2e%2e/src/secret.ts", "/assets/%2e%2e/%2e%2e/src/secret.ts",
                                   "/src/secret.ts", "/.env", "/assets/..%5c..%5csrc%5csecret.ts", "/missing.svg"])
def test_no_source_hidden_files_or_path_traversal(build_fixture, path):
    application, _dist = build_fixture
    response = application.test_client().get(path)
    assert response.status_code == 404
    assert b"synthetic source" not in response.data


def test_build_symlinks_are_never_exposed(build_fixture, tmp_path):
    application, dist = build_fixture
    secret = tmp_path / "outside.txt"
    secret.write_text("synthetic private outside build")
    (dist / "assets" / "leak.txt").symlink_to(secret)
    assert application.test_client().get("/assets/leak.txt").status_code == 404
    linked = tmp_path / "linked-dist"
    linked.symlink_to(dist, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        install_spa(Flask("linked", static_folder=None), linked)
    with pytest.raises(ValueError, match="missing"):
        install_spa(Flask("missing", static_folder=None), tmp_path / "missing-dist")


def test_api_and_production_factories_do_not_connect_or_initialize(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("factory must not connect/create directories/initialize data")

    event.listen(Engine, "connect", forbidden)
    try:
        monkeypatch.setattr(Path, "mkdir", forbidden)
        api_application = create_app(production_config(tmp_path))
        assert api_application.test_client().get("/api/health").status_code == 200
        assert not any(rule.rule.startswith("/static/") for rule in api_application.url_map.iter_rules())
        static_hook = Mock(side_effect=lambda application: application)
        monkeypatch.setattr(wsgi, "install_spa", static_hook)
        application = wsgi.create_app(production_config(tmp_path))
        assert application.config["APP_ENV"] == "production"
        static_hook.assert_called_once_with(application)
        assert not (tmp_path / "uploads").exists()
    finally:
        event.remove(Engine, "connect", forbidden)


@pytest.mark.parametrize("extra", [{"JWT_SECRET_KEY": "short"}, {"WALLET_MODE": "simulation"}, {"DEBUG": True},
                                   {"DEMO_SEED": True}, {"TESTING": True}])
def test_production_rejects_weak_keys_debug_seed_simulated_money(tmp_path, extra):
    with pytest.raises(ValueError):
        wsgi.create_app(production_config(tmp_path, **extra))


def test_wsgi_requires_explicit_production(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(ValueError, match="explicit APP_ENV"):
        wsgi.create_app()
    with pytest.raises(ValueError, match="explicit APP_ENV"):
        wsgi.create_app({"APP_ENV": "development"})


def test_start_requires_explicit_mode_and_never_manages_mysql(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "PYTHON_BIN": "/bin/true"}
    for args in [[], ["wrong-mode"], ["prod", "extra"]]:
        result = subprocess.run(["bash", str(ROOT / "start.sh"), *args], env=env, cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 64
    help_result = subprocess.run(["bash", str(ROOT / "start.sh"), "--help"], env=env, cwd=tmp_path, capture_output=True, text=True)
    assert help_result.returncode == 0
    refused = subprocess.run(["bash", str(ROOT / "start.sh"), "prod"], env=env, cwd=tmp_path, capture_output=True, text=True)
    assert refused.returncode == 1 and "APP_ENV=production" in refused.stderr
    script = (ROOT / "start.sh").read_text()
    for prohibited in ["service mysql", "mysqladmin", "sudo", "nohup", "/tmp/zhihe", "killall", "vite preview"]:
        assert prohibited not in script
    assert "127.0.0.1" in script and "gunicorn" in script and "wsgi:create_app()" in script


def test_prod_launch_execs_gunicorn_not_vite_or_data_initialization(tmp_path):
    # A fake interpreter observes the actual shell's exec arguments without
    # launching services, touching uploads, or connecting to any database.
    fake = tmp_path / "python-fixture"
    capture = tmp_path / "arguments"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    fake.chmod(0o700)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHON_BIN": str(fake), "APP_ENV": "production", "CAPTURE": str(capture)}
    # This uses a synthetic startup tree so it is independent of whether the
    # real frontend has been built yet and never writes its dist directory.
    fixture_root = tmp_path / "runtime"
    (fixture_root / "frontend" / "dist").mkdir(parents=True)
    (fixture_root / "frontend" / "dist" / "index.html").write_text("synthetic build")
    copied_script = fixture_root / "start.sh"
    copied_script.write_text((ROOT / "start.sh").read_text())
    result = subprocess.run(["bash", str(copied_script), "prod"], env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    arguments = capture.read_text().splitlines()
    assert arguments[:2] == ["-m", "gunicorn"]
    assert arguments[-1] == "wsgi:create_app()"
    assert "127.0.0.1:6000" in arguments
    assert not any("migrate" in arg or "seed" in arg or "vite" in arg for arg in arguments)
    assert not (fixture_root / "backend" / "uploads").exists()
