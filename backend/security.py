"""Versioned credentials and a shared, bounded database rate limiter."""
import hashlib
import hmac
import ipaddress
import json
import unicodedata
from dataclasses import dataclass
from functools import wraps
from datetime import datetime, timezone, timedelta
from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required, verify_jwt_in_request
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from extensions import db, jwt
from models import User, RevokedToken, RateLimitGuard, RateLimitBucket, utcnow


class APIError(Exception):
    def __init__(self, message, status=400, code=None):
        self.message, self.status, self.code = message, status, code
        super().__init__(message)


@dataclass(frozen=True)
class Identity:
    uid: int
    token_version: int
    jti: str
    expires_at: datetime


def request_identity():
    claims = get_jwt()
    raw = get_jwt_identity()
    version = claims.get("token_version")
    if not isinstance(raw, str) or not 1 <= len(raw) <= 10 or not raw.isdecimal() or not 0 < int(raw) < 2**31:
        raise APIError("登录状态无效，请重新登录", 401)
    if type(version) is not int or version < 0 or not isinstance(claims.get("jti"), str) or not 1 <= len(claims["jti"]) <= 64:
        raise APIError("登录凭证版本无效，请重新登录", 401)
    return Identity(int(raw), version, claims["jti"],
                    datetime.fromtimestamp(claims["exp"], timezone.utc).replace(tzinfo=None))


def lock_existing_child(session, model, primary_key, *conditions):
    """Fresh discovery under the caller's stable parent, then an existing PK lock.

    Every writer for these child keys holds the same User/Toolkit parent first;
    callers have not yet written the child. Do not lock a missing unique key:
    RR supremum/gap locks for unrelated parents can deadlock on later inserts.
    A separate connection also avoids inheriting an earlier RR snapshot from
    the business transaction. Existing rows are immutable in key/ownership.
    """
    with Session(db.engine) as discovery:
        key = discovery.execute(select(primary_key).where(*conditions)).scalar_one_or_none()
    if key is None:
        return None
    return session.execute(select(model).where(primary_key == key).with_for_update()
                           .execution_options(populate_existing=True)).scalar_one_or_none()


def assert_identity(session, user, identity):
    if not user or user.token_version != identity.token_version or identity.expires_at <= utcnow():
        raise APIError("登录已失效，请重新登录", 401)
    # User is already locked. Logout/password changes take that same parent;
    # live token revocations cannot be pruned before this identity expires.
    revoked = lock_existing_child(session, RevokedToken, RevokedToken.jti, RevokedToken.jti == identity.jti)
    if revoked:
        raise APIError("登录已失效，请重新登录", 401)


def current_user():
    identity = request_identity()
    user = db.session.execute(select(User).where(User.id == identity.uid).execution_options(populate_existing=True)).scalar_one_or_none()
    if not user or user.token_version != identity.token_version:
        raise APIError("登录已失效，请重新登录", 401)
    return user


def optional_user():
    # Only genuinely absent credentials are anonymous; invalid optional JWTs
    # propagate to JWT callbacks instead of silently changing access policy.
    header_name = current_app.config.get("JWT_HEADER_NAME", "Authorization")
    verify_jwt_in_request(optional=header_name not in request.headers)
    return current_user() if get_jwt_identity() is not None else None


def optional_user_id():
    user = optional_user()
    return user.id if user else None


def admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        if current_user().role != "admin":
            raise APIError("需要超级管理员权限", 403)
        return fn(*args, **kwargs)
    return wrapper


def json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise APIError("请求正文必须是 JSON 对象", 400)
    return data


def text(value, field, maximum, minimum=0, strip=True):
    if not isinstance(value, str):
        raise APIError(f"{field}必须是字符串")
    if len(value) > maximum:
        raise APIError(f"{field}不能超过 {maximum} 字")
    result = value.strip() if strip else value
    if not minimum <= len(result) <= maximum:
        raise APIError(f"{field}长度须在 {minimum}～{maximum} 字之间")
    return result


def positive_id(value, field="参数"):
    if type(value) is not int or not 0 < value < 2**31:
        raise APIError(f"{field}须为有效整数")
    return value


def secret_digest(value):
    secret = current_app.config["RATE_LIMIT_SECRET"]
    return hmac.new(str(secret).encode(), value.encode(), hashlib.sha256).hexdigest()


def client_ip():
    peer = request.remote_addr or "unknown"
    cidrs = current_app.config.get("TRUSTED_PROXY_CIDRS", [])
    if isinstance(cidrs, str):
        cidrs = [x.strip() for x in cidrs.split(",") if x.strip()]
    try:
        networks = [ipaddress.ip_network(x) for x in cidrs]
        trusted = lambda address: any(ipaddress.ip_address(address) in net for net in networks)
        if trusted(peer) and request.headers.get("X-Forwarded-For"):
            chain = [x.strip() for x in request.headers["X-Forwarded-For"].split(",")]
            if len(chain) > 20:
                raise APIError("代理地址链无效")
            for address in reversed(chain):
                ipaddress.ip_address(address)
                if not trusted(address):
                    return address
            return chain[0]
    except ValueError:
        raise APIError("代理地址配置或地址格式无效")
    return peer


def account_rate_key(username):
    # Equality is resolved by the database, not Python lower(): MySQL's actual
    # username collation (including unicode_ci accents/case) defines accounts.
    with Session(db.engine) as session:
        uid = session.execute(select(User.id).where(User.username == username)).scalar_one_or_none()
    if uid is not None:
        return f"user:{uid}"
    normalized = "".join(x for x in unicodedata.normalize("NFKD", username.casefold()) if not unicodedata.combining(x))
    return f"unknown:{normalized}"


def _ensure_guard(session):
    dialect = session.get_bind().dialect.name
    if dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert
        stmt = insert(RateLimitGuard).values(id=1)
        session.execute(stmt.on_duplicate_key_update(id=stmt.inserted.id))
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
        session.execute(insert(RateLimitGuard).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    else:
        raise APIError("限流存储暂不可用", 503, "RATE_LIMIT_UNAVAILABLE")
    session.execute(select(RateLimitGuard).where(RateLimitGuard.id == 1).with_for_update()).scalar_one()


def rate_hits(specs):
    """Consume all (key, limit, window) atomically in a separate transaction.

    Fixed TTL windows are anchored to first hit. A single persistent guard
    protects both the global capacity and all bucket changes across workers.
    """
    try:
        with Session(db.engine) as session, session.begin():
            _ensure_guard(session)
            now = utcnow()
            session.execute(delete(RateLimitBucket).where(RateLimitBucket.expires_at <= now))
            capacity = current_app.config["RATE_LIMIT_CAPACITY"]
            count = session.execute(select(db.func.count()).select_from(RateLimitBucket)).scalar_one()
            buckets = []
            for key, limit, window in specs:
                digest = secret_digest(f"{window}:{key}")
                row = session.execute(select(RateLimitBucket).where(RateLimitBucket.key_digest == digest)
                                      .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
                if row is None:
                    if count >= capacity:
                        raise APIError("请求过于频繁，请稍后再试", 429, "RATE_LIMIT_CAPACITY")
                    row = RateLimitBucket(key_digest=digest, hits=0, expires_at=now + timedelta(seconds=window))
                    session.add(row)
                    count += 1
                if row.hits >= limit:
                    raise APIError("尝试次数过多，请稍后再试", 429, "RATE_LIMITED")
                buckets.append(row)
            for row in buckets:
                row.hits += 1
    except SQLAlchemyError:
        raise APIError("限流存储暂不可用，请稍后再试", 503, "RATE_LIMIT_UNAVAILABLE") from None


def rate_hit(key, limit, window=60):
    rate_hits([(key, limit, window)])
    return True


def configure_jwt():
    @jwt.token_in_blocklist_loader
    def token_revoked(_header, payload):
        raw = payload.get("sub")
        version = payload.get("token_version")
        jti = payload.get("jti")
        if not isinstance(raw, str) or not 1 <= len(raw) <= 10 or not raw.isdecimal() or not 0 < int(raw) < 2**31 or type(version) is not int or not isinstance(jti, str) or not 1 <= len(jti) <= 64:
            return True
        user = db.session.execute(select(User).where(User.id == int(raw)).execution_options(populate_existing=True)).scalar_one_or_none()
        return not user or user.token_version != version or db.session.get(RevokedToken, jti) is not None

    @jwt.unauthorized_loader
    def unauthorized(_reason):
        return jsonify({"message": "请先登录"}), 401

    @jwt.invalid_token_loader
    def invalid(_reason):
        return jsonify({"message": "登录状态无效，请重新登录"}), 401

    @jwt.expired_token_loader
    def expired(_header, _payload):
        return jsonify({"message": "登录已过期，请重新登录"}), 401

    @jwt.revoked_token_loader
    def revoked(_header, _payload):
        return jsonify({"message": "登录已失效，请重新登录"}), 401
