"""Explicit application configuration; no dotenv/database/filesystem IO on import."""
import os
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

WEAK_KEYS = {"", "dev-only-change-me", "replace-with-a-long-random-secret", "dev-local-secret-please-replace", "test-secret"}


def _bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"1", "true", "yes", "on"}:
        return True
    if isinstance(value, str) and value.lower() in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("Boolean configuration must be true/false")


def build_config(config_override=None):
    # An injected configuration deliberately does not inspect the real .env or
    # environment. Integration tests inject their isolated database explicitly.
    source = dict(os.environ) if config_override is None else dict(config_override)
    env = source.get("APP_ENV", "test" if source.get("TESTING") else "development")
    if env not in {"development", "test", "production"}:
        raise ValueError("APP_ENV must be development, test or production")
    if config_override is None and _bool(source.get("LOAD_DOTENV", "0")):
        if env == "test":
            raise ValueError("Tests may not load dotenv")
        from dotenv import dotenv_values
        values = dotenv_values(Path(__file__).with_name(".env"))
        source = {**values, **source}
        # The dotenv file may supply APP_ENV. Validate the effective merged
        # mode before applying any production gates or choosing defaults.
        env = source.get("APP_ENV", "test" if _bool(source.get("TESTING", "0")) else "development")
        if env not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test or production")
        if env == "test":
            raise ValueError("Tests may not load dotenv")
    test = env == "test"
    password = quote_plus(source.get("MYSQL_PASSWORD", ""))
    uri = source.get("SQLALCHEMY_DATABASE_URI") or source.get("DATABASE_URL")
    if not uri and test:
        raise ValueError("Tests must explicitly provide an isolated database URI")
    if env == "production" and not uri and not all(source.get(key) for key in (
            "MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD")):
        raise ValueError("Production requires an explicit database URI or nonempty MYSQL_HOST/DATABASE/USER/PASSWORD")
    uri = uri or (f"mysql+pymysql://{source.get('MYSQL_USER', 'root')}:{password}@"
                  f"{source.get('MYSQL_HOST', '127.0.0.1')}:{source.get('MYSQL_PORT', '3306')}/"
                  f"{source.get('MYSQL_DATABASE', 'zhihe')}?charset=utf8mb4")
    if test and not source.get("UPLOAD_FOLDER"):
        raise ValueError("Tests must explicitly provide a temporary UPLOAD_FOLDER")
    mode = source.get("WALLET_MODE", "disabled")
    if mode not in {"disabled", "simulation"}:
        raise ValueError("WALLET_MODE must be disabled or simulation; no payment provider is configured")
    config = {
        "APP_ENV": env, "TESTING": test, "DEBUG": _bool(source.get("DEBUG", source.get("FLASK_DEBUG", "0"))),
        "SQLALCHEMY_DATABASE_URI": uri, "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "JWT_SECRET_KEY": source.get("JWT_SECRET_KEY", "dev-only-change-me"),
        "JWT_ACCESS_TOKEN_EXPIRES": timedelta(days=int(source.get("JWT_EXPIRE_DAYS", 1))),
        "JWT_TOKEN_LOCATION": ["headers"], "MAX_CONTENT_LENGTH": 100 * 1024 * 1024,
        "UPLOAD_FOLDER": str(source.get("UPLOAD_FOLDER") or Path(__file__).with_name("uploads")),
        "FRONTEND_ORIGIN": source.get("FRONTEND_ORIGIN", "http://localhost:4000"),
        "WALLET_MODE": mode, "DEMO_SEED": _bool(source.get("DEMO_SEED", "0")),
        "TRUSTED_PROXY_CIDRS": source.get("TRUSTED_PROXY_CIDRS", []),
        "RATE_LIMIT_CAPACITY": int(source.get("RATE_LIMIT_CAPACITY", 10000)),
        "RATE_LIMIT_SECRET": source.get("RATE_LIMIT_SECRET") or source.get("JWT_SECRET_KEY", "dev-only-change-me"),
        "FILE_STORAGE_BACKEND": source.get("FILE_STORAGE_BACKEND", "local"),
        "S3_BUCKET": source.get("S3_BUCKET"),
        "S3_REGION": source.get("S3_REGION"),
        "S3_ENDPOINT_URL": source.get("S3_ENDPOINT_URL"),
        "S3_ACCESS_KEY_ID": source.get("S3_ACCESS_KEY_ID"),
        "S3_SECRET_ACCESS_KEY": source.get("S3_SECRET_ACCESS_KEY"),
        "S3_SESSION_TOKEN": source.get("S3_SESSION_TOKEN"),
        "FILE_USER_QUOTA": int(source.get("FILE_USER_QUOTA", 1024 * 1024 * 1024)),
        "FILE_MAX_SIZE": int(source.get("FILE_MAX_SIZE", 40 * 1024 * 1024)),
        "FILE_OPERATION_GRACE_SECONDS": int(source.get("FILE_OPERATION_GRACE_SECONDS", 3600)),
        "IMAGE_MAX_PIXELS": int(source.get("IMAGE_MAX_PIXELS", 40_000_000)),
        "IMAGE_MAX_DIMENSION": int(source.get("IMAGE_MAX_DIMENSION", 16000)),
    }
    if config_override is not None:
        config.update(config_override)
    # These protections are not overrideable, including in tests.
    config["APP_ENV"] = env
    config["JWT_TOKEN_LOCATION"] = ["headers"]
    secret = config["JWT_SECRET_KEY"]
    if env != "test" and (not isinstance(secret, str) or secret in WEAK_KEYS or len(secret) < 32 or
                          secret.lower().startswith(("replace-", "change_me", "dev-only-", "dev-local-"))):
        raise ValueError("Non-test applications require an explicit strong JWT_SECRET_KEY of at least 32 characters")
    if env == "production":
        if config["DEBUG"] or config["TESTING"] or config["DEMO_SEED"]:
            raise ValueError("Production forbids debug, testing and demo seed")
        # WALLET_MODE=simulation is the deliberate launch mode until a real
        # payment provider is integrated: wallet money is a non-cash demo
        # balance and every UI path labels it as simulated trading. Setting
        # WALLET_MODE=disabled keeps the strict no-money behaviour instead.
    if config["RATE_LIMIT_CAPACITY"] < 1:
        raise ValueError("RATE_LIMIT_CAPACITY must be positive")
    if config["FILE_STORAGE_BACKEND"] not in {"local", "s3"}:
        raise ValueError("FILE_STORAGE_BACKEND must be local or s3")
    if config["FILE_STORAGE_BACKEND"] == "s3":
        bucket, region = config["S3_BUCKET"], config["S3_REGION"]
        if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise ValueError("S3 storage requires a valid S3_BUCKET")
        if not isinstance(region, str) or not re.fullmatch(r"[A-Za-z0-9-]{2,40}", region):
            raise ValueError("S3 storage requires an explicit S3_REGION")
        access_key, secret_key = config["S3_ACCESS_KEY_ID"], config["S3_SECRET_ACCESS_KEY"]
        if bool(access_key) != bool(secret_key) or (config["S3_SESSION_TOKEN"] and not access_key):
            raise ValueError("Explicit S3 credentials require access key and secret key together")
        endpoint = config["S3_ENDPOINT_URL"]
        if endpoint:
            parsed = urlsplit(endpoint)
            if (parsed.scheme not in {"https", "http"} or not parsed.hostname or
                    parsed.username or parsed.password or parsed.query or parsed.fragment or
                    (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"})):
                raise ValueError("S3_ENDPOINT_URL requires HTTPS or a loopback HTTP endpoint")
    return config
