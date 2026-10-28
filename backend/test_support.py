"""Owned, temporary MySQL fixtures. Importing this module never starts a server."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote
import uuid


class IsolatedMySQL:
    """Run MySQL on a private Unix socket, with no TCP or existing configuration."""

    def __init__(self):
        self.run_id = uuid.uuid4().hex
        self.database = f"zhihe_test_{self.run_id[:16]}"
        self._temp = None
        self._process = None
        self._log = None
        self.root = None
        self.socket = None

    @property
    def uri(self):
        if self._process is None or self._process.poll() is not None:
            raise RuntimeError("The owned test MySQL instance is not running")
        return (f"mysql+pymysql://root@localhost/{self.database}"
                f"?unix_socket={quote(str(self.socket), safe='')}&charset=utf8mb4")

    def connect(self, database=None):
        import pymysql
        if self.socket is None:
            raise RuntimeError("No isolated MySQL socket")
        return pymysql.connect(user="root", password="", unix_socket=str(self.socket),
                               database=database, charset="utf8mb4", autocommit=True,
                               connect_timeout=2, read_timeout=15, write_timeout=15)

    def _tail_log(self):
        if not self.root:
            return ""
        path = self.root / "mysql.log"
        if not path.exists():
            return ""
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 6000))
            return stream.read().decode("utf-8", errors="replace")

    def __enter__(self):
        binary = shutil.which("mysqld")
        if not binary:
            raise RuntimeError("MySQL concurrency acceptance requires mysqld; it cannot be skipped")
        self._temp = tempfile.TemporaryDirectory(prefix="zhihe-qa-")
        self.root = Path(self._temp.name)
        self.root.chmod(0o700)
        self.socket = self.root / "mysql.sock"
        data = self.root / "data"
        data.mkdir(mode=0o700)
        files = self.root / "files"
        files.mkdir(mode=0o700)
        self._log = (self.root / "mysql.log").open("ab", buffering=0)
        common = [binary, "--no-defaults", f"--datadir={data}", "--innodb-buffer-pool-size=32M"]
        try:
            result = subprocess.run(common + ["--initialize-insecure"], stdout=self._log,
                                    stderr=subprocess.STDOUT, timeout=120, check=False)
            if result.returncode:
                raise RuntimeError(f"Cannot initialize isolated MySQL:\n{self._tail_log()}")
            args = common + [f"--socket={self.socket}", f"--pid-file={self.root / 'mysql.pid'}",
                             "--skip-networking", "--mysqlx=OFF", "--skip-log-bin",
                             "--local-infile=0", f"--secure-file-priv={files}",
                             "--max-connections=40", "--innodb-lock-wait-timeout=8", "--innodb-use-native-aio=0",
                             "--transaction-isolation=REPEATABLE-READ"]
            self._process = subprocess.Popen(args, stdout=self._log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise RuntimeError(f"Isolated MySQL exited:\n{self._tail_log()}")
                try:
                    connection = self.connect()
                except Exception:
                    time.sleep(0.15)
                    continue
                with connection:
                    with connection.cursor() as cursor:
                        if not re.fullmatch(r"zhihe_test_[a-f0-9]{16}", self.database):
                            raise RuntimeError("Unsafe fixture database name")
                        cursor.execute(f"CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                        cursor.execute(f"CREATE TABLE `{self.database}`.__zhihe_test_marker (run_id VARCHAR(32) PRIMARY KEY)")
                        cursor.execute(f"INSERT INTO `{self.database}`.__zhihe_test_marker VALUES (%s)", (self.run_id,))
                self.assert_owned()
                return self
            raise RuntimeError(f"Isolated MySQL startup timed out:\n{self._tail_log()}")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def assert_owned(self):
        if self._process is None or self._process.poll() is not None:
            raise RuntimeError("Fixture server ownership is missing")
        with self.connect(self.database) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT run_id FROM __zhihe_test_marker")
                if cursor.fetchall() != ((self.run_id,),):
                    raise RuntimeError("Database isolation marker does not match")
                cursor.execute("SELECT @@datadir, @@socket, @@transaction_isolation")
                datadir, socket, isolation = cursor.fetchone()
                if (Path(datadir).resolve() != (self.root / "data").resolve()
                        or Path(socket).resolve() != self.socket.resolve()
                        or isolation != "REPEATABLE-READ"):
                    raise RuntimeError("Test database is not the owned RR/Unix-socket instance")

    def __exit__(self, *_exc):
        # Only the exact subprocess started above is stopped. Never killall/service.
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=10)
            self._process = None
        if self._log is not None:
            self._log.close()
            self._log = None
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None


def isolated_config(uri, uploads):
    """No .env fallbacks, public credentials, or real upload directory."""
    root = Path(uploads).resolve()
    real_uploads = Path(__file__).with_name("uploads").resolve()
    if root == real_uploads or real_uploads in root.parents or root in real_uploads.parents:
        raise ValueError("Tests may not use or contain the business uploads directory")
    if not str(uri).startswith(("sqlite:///:memory:", "mysql+pymysql://root@localhost/zhihe_test_")):
        raise ValueError("A fixture must use memory SQLite or an owned test MySQL schema")
    return {"APP_ENV": "test", "TESTING": True, "SQLALCHEMY_DATABASE_URI": uri,
            "UPLOAD_FOLDER": str(root), "JWT_SECRET_KEY": uuid.uuid4().hex + uuid.uuid4().hex,
            "WALLET_MODE": "simulation", "DEMO_SEED": False}
