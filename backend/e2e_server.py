"""Test-only server. Production factories never register these fixture endpoints."""
import json
from pathlib import Path
import secrets
import tempfile
import threading

from test_fixtures import build_dataset
from test_support import isolated_config


class QAServer:
    def __init__(self, mysql, dist_path=None):
        self.mysql = mysql
        self.dist_path = dist_path
        self._temp = None
        self._server = None
        self._thread = None
        self._reset_lock = threading.Lock()
        self.app = None
        self.data = None
        self.origin = None
        self.fixture_path = None

    def _reset(self):
        from extensions import db
        self.mysql.assert_owned()
        if self.app.config["SQLALCHEMY_DATABASE_URI"] != self.mysql.uri:
            raise RuntimeError("QA schema is not the owned MySQL fixture")
        with self._reset_lock:
            with self.app.app_context():
                db.session.remove()
                db.drop_all()
                db.create_all()
                self.data = build_dataset(self.app, self.mysql.run_id)
                self.data["origin"] = self.origin
        return self.data

    def __enter__(self):
        from app import create_app
        from flask import jsonify, request
        from static_spa import install_spa
        from werkzeug.serving import make_server, WSGIRequestHandler

        class QuietRequests(WSGIRequestHandler):
            def log_request(self, *_args):
                pass

        self.mysql.assert_owned()
        self._temp = tempfile.TemporaryDirectory(prefix="zhihe-e2e-")
        root = Path(self._temp.name)
        root.chmod(0o700)
        self.fixture_path = root / "fixtures.json"
        try:
            self.app = create_app(isolated_config(self.mysql.uri, root / "uploads"))
            install_spa(self.app, self.dist_path)

            @self.app.get("/api/__qa__/ready")
            def qa_ready():
                self.mysql.assert_owned()
                return jsonify({"runId": self.mysql.run_id})

            @self.app.post("/api/__qa__/reset")
            def qa_reset():
                if not secrets.compare_digest(request.headers.get("X-Zhihe-QA", ""), self.mysql.run_id):
                    return jsonify({"message": "Fixture ownership confirmation required"}), 403
                return jsonify(self._reset())

            self._server = make_server("127.0.0.1", 0, self.app, threaded=True, request_handler=QuietRequests)
            self.origin = f"http://127.0.0.1:{self._server.server_port}"
            self._reset()
            with self.fixture_path.open("x", encoding="utf-8") as stream:
                json.dump(self.data, stream, ensure_ascii=False)
            self.fixture_path.chmod(0o600)
            self._thread = threading.Thread(target=self._server.serve_forever, name="zhihe-owned-qa", daemon=True)
            self._thread.start()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_exc):
        if self._server is not None:
            if self._thread is not None and self._thread.is_alive():
                self._server.shutdown()
                self._thread.join(timeout=15)
                if self._thread.is_alive():
                    raise RuntimeError("Owned QA server did not stop")
            self._server.server_close()
            self._server = None
        if self.app is not None:
            from extensions import db
            with self.app.app_context():
                db.session.remove()
                db.engine.dispose()
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None
