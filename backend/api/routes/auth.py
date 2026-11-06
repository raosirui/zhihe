"""Authentication and account endpoints."""
import re

from flask import jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from api import api
from api.support import _token
from extensions import db
from models import RevokedToken, User
from security import (APIError, account_rate_key, assert_identity, client_ip, current_user,
                      json_object, rate_hits, request_identity, text)
import money_service as money


@api.post("/api/auth/login")
def login():
    data = json_object()
    account_input = text(data.get("username", "") or data.get("email", ""), "账号", 120, 1)
    password = text(data.get("password", ""), "密码", 128, 1, strip=False)
    ip = client_ip()
    try:
        account = account_rate_key(account_input)
    except Exception as error:
        from sqlalchemy.exc import SQLAlchemyError
        if isinstance(error, SQLAlchemyError):
            raise APIError("认证服务暂不可用", 503) from error
        raise
    rate_hits([(f"login:ip:{ip}", 30, 60), (f"login:account:{account}", 8, 60),
               (f"login:ip:{ip}", 100, 600), (f"login:account:{account}", 30, 600)])
    # Try to find user by username first, then by email
    user = User.query.filter_by(username=account_input).first()
    if not user:
        user = User.query.filter_by(email=account_input).first()
    if not user or not check_password_hash(user.password_hash, password):
        raise APIError("账号或密码不正确", 401)
    return jsonify({"token": _token(user), "user": user.public()})


@api.post("/api/auth/register")
def register():
    data = json_object()
    username = text(data.get("username", ""), "账号", 50, 3)
    email = text(data.get("email", ""), "邮箱", 120, 0)
    password = text(data.get("password", ""), "密码", 128, 8, strip=False)
    if not re.fullmatch(r"[\w.\-·]+", username):
        raise APIError("账号需为 3～50 位字母、数字或 _.- 字符")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise APIError("邮箱格式不正确")
    ip = client_ip()
    rate_hits([(f"register:{ip}", 5, 60), (f"register:{ip}", 20, 600)])
    try:
        with Session(db.engine, expire_on_commit=False) as session, session.begin():
            if session.execute(select(User.id).where(User.username == username)).first():
                raise APIError("账号已存在", 409)
            if email and session.execute(select(User.id).where(User.email == email)).first():
                raise APIError("邮箱已被注册", 409)
            # Use username as nickname
            user = User(username=username, email=email or None, nickname=username,
                       password_hash=generate_password_hash(password), role="user")
            session.add(user)
            session.flush()
            result, token = user.public(), _token(user)
    except IntegrityError:
        raise APIError("账号已存在", 409) from None
    return jsonify({"token": token, "user": result}), 201


@api.get("/api/auth/me")
@jwt_required()
def me():
    return jsonify(current_user().public())


@api.post("/api/auth/logout")
@jwt_required()
def logout():
    identity = request_identity()
    with Session(db.engine) as session, session.begin():
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        if session.get(RevokedToken, identity.jti) is None:
            session.add(RevokedToken(jti=identity.jti, user_id=user.id, expires_at=identity.expires_at))
    return jsonify({"message": "已退出登录"})


@api.put("/api/auth/password")
@jwt_required()
def change_password():
    data = json_object()
    old = text(data.get("oldPassword", ""), "原密码", 128, 1, strip=False)
    new = text(data.get("newPassword", ""), "新密码", 128, 8, strip=False)
    identity = request_identity()
    with Session(db.engine) as session, session.begin():
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        if not check_password_hash(user.password_hash, old):
            raise APIError("原密码不正确")
        user.password_hash = generate_password_hash(new)
        user.token_version += 1
        session.add(RevokedToken(jti=identity.jti, user_id=user.id, expires_at=identity.expires_at))
    return jsonify({"message": "密码已修改，请重新登录"})
