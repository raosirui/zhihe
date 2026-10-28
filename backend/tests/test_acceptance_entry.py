"""The acceptance runner cannot turn missing/skipped checks into green results."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from acceptance_test import verify_playwright_report, verify_pytest_report, verify_vitest_report


def test_test_entry_import_has_no_network_database_or_dotenv_io():
    script = '''
import socket
import dotenv
import sqlalchemy

def forbidden(*args, **kwargs):
    raise AssertionError("Test entry import performed external IO")

socket.socket.connect = forbidden
dotenv.dotenv_values = forbidden
dotenv.load_dotenv = forbidden
sqlalchemy.create_engine = forbidden
import acceptance_test
import smoke_test
import migrate
import test_support
import e2e_server
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("body", ["", "<skipped type='pytest.xfail'/>", "<error/>", "<failure/>"])
def test_backend_report_requires_complete_tests(tmp_path, body):
    path = tmp_path / "report.xml"
    path.write_text(f"<testsuites><testsuite><testcase>{body}</testcase></testsuite></testsuites>")
    if body:
        with pytest.raises(RuntimeError):
            verify_pytest_report(path)
    else:
        assert verify_pytest_report(path) == {"testcases": 1, "skipped": 0}
    path.write_text("<testsuites><testsuite tests='0'/></testsuites>")
    with pytest.raises(RuntimeError):
        verify_pytest_report(path)


@pytest.mark.parametrize("key", ["numFailedTests", "numPendingTests", "numTodoTests", "numFailedTestSuites"])
def test_component_report_rejects_incomplete_acceptance(tmp_path, key):
    path = tmp_path / "components.json"
    path.write_text(json.dumps({"success": True, "numPassedTests": 10, key: 1}))
    with pytest.raises(RuntimeError):
        verify_vitest_report(path)
    path.write_text(json.dumps({"success": True, "numPassedTests": 10}))
    assert verify_vitest_report(path) == {"passed": 10, "skipped": 0}


@pytest.mark.parametrize("key", ["unexpected", "flaky", "skipped"])
def test_browser_report_rejects_failures_skips_and_flaky_retries(tmp_path, key):
    path = tmp_path / "browser.json"
    path.write_text(json.dumps({"stats": {"expected": 9, key: 1}}))
    with pytest.raises(RuntimeError):
        verify_playwright_report(path)
    path.write_text(json.dumps({"stats": {"expected": 9, "unexpected": 0, "flaky": 0, "skipped": 0}}))
    assert verify_playwright_report(path) == {"passed": 9, "skipped": 0}


def test_qa_management_routes_do_not_exist_in_application_factory(sqlite_app):
    with sqlite_app.test_client() as client:
        assert client.get("/api/__qa__/ready").status_code == 404
        assert client.post("/api/__qa__/reset", headers={"X-Zhihe-QA": "anything"}).status_code == 404


@pytest.mark.parametrize("root_version", ["1.0", "1.2"])
def test_audit_uses_installed_dependency_closure_and_rejects_wrong_pins(tmp_path, monkeypatch, root_version):
    import acceptance_test
    from importlib import metadata
    (tmp_path / "requirements.txt").write_text("Root[feature]==1.0\n")
    (tmp_path / "requirements-dev.txt").write_text("-r requirements.txt\n")
    monkeypatch.setattr(acceptance_test, "BACKEND", tmp_path)
    versions = {"root": root_version, "child": "2.1"}
    monkeypatch.setattr(metadata, "version", lambda name: versions[name])
    monkeypatch.setattr(metadata, "requires", lambda name: ["Child>=2; extra == 'feature'", "Unavailable; python_version < '3.0'"] if name == "root" else [])
    if root_version != "1.0":
        with pytest.raises(RuntimeError, match="does not satisfy"):
            acceptance_test.installed_python_requirements(tmp_path)
    else:
        path, found = acceptance_test.installed_python_requirements(tmp_path)
        assert found == {"root": "1.0", "child": "2.1"}
        assert path.read_text() == "child==2.1\nroot==1.0\n"


def test_acceptance_rejects_symlinked_report_root_before_starting_stages(tmp_path, monkeypatch):
    import acceptance_test
    target = tmp_path / "untouched"
    target.mkdir()
    (tmp_path / ".test-artifacts").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(acceptance_test, "ROOT", tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A stage ran before report isolation was verified")

    monkeypatch.setattr(acceptance_test, "run_stage", forbidden)
    with pytest.raises(RuntimeError, match="symbolic link"):
        acceptance_test.main([])
    assert list(target.iterdir()) == []
