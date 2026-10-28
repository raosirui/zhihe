"""Explicit full acceptance. Never connect to a pre-existing HTTP service or database."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend"


def run_stage(name, command, directory, reports, results, env=None, timeout=600):
    print(f"\n== {name} ==", flush=True)
    started = time.monotonic()
    result = {"name": name, "status": "running"}
    results.append(result)
    # A fresh process group contains only this invocation and its children.
    # Inherited stdout remains visible, with a separate JSON/XML test report.
    process = subprocess.Popen(command, cwd=directory, env=env, start_new_session=True)
    try:
        code = process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        result.update(status="failed", reason="timeout or interruption")
        raise RuntimeError(f"{name}: timed out or interrupted") from None
    result.update(seconds=round(time.monotonic() - started, 2), returncode=code,
                  status="passed" if code == 0 else "failed")
    if code:
        raise RuntimeError(f"{name}: exited with code {code}")
    return result


def installed_python_requirements(reports):
    from importlib import metadata
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    roots = [Requirement(line.strip()) for path in (BACKEND / "requirements.txt", BACKEND / "requirements-dev.txt")
             for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith(("#", "-"))]
    pending, seen, versions = list(roots), set(), {}
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            raise RuntimeError(f"Required Python package {name} is not installed") from None
        if requirement.specifier and not requirement.specifier.contains(version, prereleases=True):
            raise RuntimeError(f"Installed {name} {version} does not satisfy {requirement.specifier}; install requirements first")
        versions[name] = version
        key = (name, frozenset(requirement.extras))
        if key in seen:
            continue
        seen.add(key)
        for raw in metadata.requires(name) or []:
            child = Requirement(raw)
            if child.marker is None or any(child.marker.evaluate({"extra": extra}) for extra in {"", *requirement.extras}):
                pending.append(child)
    path = reports / "python-installed.txt"
    with path.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(f"{name}=={version}" for name, version in sorted(versions.items())) + "\n")
    return path, versions


def verify_pytest_report(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    if not cases or any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise RuntimeError("Backend acceptance requires actual test cases with no failures, errors, skips or xfails")
    # Include collection/setup errors that aren't represented as ordinary cases.
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if any(int(suite.get(tag, "0")) for suite in suites for tag in ("failures", "errors", "skipped")):
        raise RuntimeError("Backend JUnit report contains incomplete acceptance")
    return {"testcases": len(cases), "skipped": 0}


def verify_vitest_report(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    passed = report.get("numPassedTests", 0)
    if not report.get("success") or passed < 1 or any(report.get(key, 0) for key in (
            "numFailedTests", "numPendingTests", "numTodoTests", "numFailedTestSuites")):
        raise RuntimeError("Frontend acceptance requires executed component tests with no skipped/pending/failed tests")
    return {"passed": passed, "skipped": 0}


def verify_playwright_report(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    stats = report.get("stats", {})
    if stats.get("expected", 0) < 1 or any(stats.get(key, 0) for key in ("unexpected", "flaky", "skipped")) or report.get("errors"):
        raise RuntimeError("Browser acceptance requires real executed tests with no failures, flaky retries or skips")
    return {"passed": stats["expected"], "skipped": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-only", action="store_true", help="Explicit subset; does NOT constitute full acceptance")
    args = parser.parse_args(argv)
    artifact_root = ROOT / ".test-artifacts"
    if artifact_root.is_symlink():
        raise RuntimeError("Acceptance artifact root may not be a symbolic link")
    artifact_root.mkdir(mode=0o700, exist_ok=True)
    reports = Path(tempfile.mkdtemp(prefix="acceptance-", dir=artifact_root))
    summary = {"startedAt": datetime.now(timezone.utc).isoformat(),
               "scope": "backend-only" if args.backend_only else "full",
               "status": "running", "stages": []}
    results = summary["stages"]
    env = {**os.environ, "PYTHONPATH": str(BACKEND), "PYTHONUNBUFFERED": "1"}
    # Test-only endpoints/fixtures are created locally below; never accept a URL
    # or fixture path supplied by the caller as an alternate E2E destination.
    env.pop("ZHIHE_E2E_API_URL", None)
    env.pop("ZHIHE_E2E_FIXTURES", None)
    try:
        audit_requirements, versions = installed_python_requirements(reports)
        summary["pythonPackages"] = versions
        junit = reports / "backend.xml"
        result = run_stage("Backend behavior, isolated MySQL concurrency and migrations", [
            sys.executable, "-m", "pytest", "-c", str(BACKEND / "pytest.ini"),
            str(BACKEND / "tests"), "-q", "--tb=short", "--strict-config", f"--junitxml={junit}"
        ], BACKEND, reports, results, env=env, timeout=900)
        result.update(verify_pytest_report(junit))
        run_stage("Isolated API smoke", [sys.executable, str(BACKEND / "smoke_test.py")],
                  BACKEND, reports, results, env=env)
        run_stage("Python public dependency security audit", [
            sys.executable, "-m", "pip_audit", "-r", str(audit_requirements), "--strict"
        ], BACKEND, reports, results, env=env)
        if not args.backend_only:
            run_stage("Installed frontend dependency consistency", ["npm", "ls", "--all"], FRONTEND, reports, results, env=env)
            run_stage("TypeScript", ["npm", "run", "typecheck"], FRONTEND, reports, results, env=env)
            run_stage("Production frontend build", ["npm", "run", "build"], FRONTEND, reports, results, env=env)
            components = reports / "components.json"
            result = run_stage("StrictMode component and session race tests", [
                "npm", "test", "--", "--reporter=default", "--reporter=json", f"--outputFile={components}"
            ], FRONTEND, reports, results, env=env)
            result.update(verify_vitest_report(components))
            # Use the public registry audit endpoint, not a mirror that may
            # silently omit advisories. Only public package metadata is sent.
            run_stage("Frontend public dependency security audit", [
                "npm", "audit", "--registry=https://registry.npmjs.org", "--audit-level=low"
            ], FRONTEND, reports, results, env=env)
            from e2e_server import QAServer
            from test_support import IsolatedMySQL
            with IsolatedMySQL() as mysql, QAServer(mysql, FRONTEND / "dist") as server:
                browsers = reports / "browser.json"
                browser_env = {**env, "ZHIHE_E2E_API_URL": server.origin,
                               "ZHIHE_E2E_FIXTURES": str(server.fixture_path),
                               "PLAYWRIGHT_JSON_OUTPUT_NAME": str(browsers)}
                result = run_stage("Browser E2E against owned MySQL and same-origin SPA", [
                    "npm", "run", "test:e2e", "--", "--reporter=line,json"
                ], FRONTEND, reports, results, env=browser_env, timeout=900)
                result.update(verify_playwright_report(browsers))
                mysql.assert_owned()
        run_stage("Shell startup syntax", ["bash", "-n", str(ROOT / "start.sh")],
                  ROOT, reports, results, env=env)
        run_stage("Patch whitespace", ["git", "diff", "--check"], ROOT, reports, results, env=env)
        summary["status"] = "passed"
    except (RuntimeError, OSError, ValueError, ET.ParseError) as error:
        if results and results[-1]["status"] == "passed":
            results[-1].update(status="failed", reason=str(error))
        summary.update(status="failed", reason=str(error))
        print(f"\nACCEPTANCE FAILED: {error}", file=sys.stderr, flush=True)
    finally:
        summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
        with (reports / "summary.json").open("x", encoding="utf-8") as stream:
            json.dump(summary, stream, ensure_ascii=False, indent=2)
        print(f"\nAcceptance report: {reports / 'summary.json'}", flush=True)
    if summary["status"] != "passed":
        return 1
    if args.backend_only:
        print("BACKEND SUBSET PASSED. Frontend/browser acceptance was not run; this is NOT full acceptance.")
    else:
        print("FULL ISOLATED ACCEPTANCE PASSED. Business database migration/deployment were not performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
