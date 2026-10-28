"""Explicit test-only data builders, never demo accounts or existing IDs."""
from decimal import Decimal
from functools import lru_cache
import hashlib
from pathlib import Path
import uuid


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="#059669"/></svg>'
RESOURCE = b"fixture resource: no executable code\n"
PASSWORDS = {"admin": "QaAdmin@123", "buyer": "QaBuyer@123", "author": "QaAuthor@123", "legacy": "Old123"}


@lru_cache(maxsize=8)
def _password_hash(password):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(password)


def build_dataset(app, run_id=None):
    from extensions import db
    from models import FileAsset, FileReference, Lesson, RateLimitGuard, Toolkit, User
    if app.config.get("APP_ENV") != "test" or not app.config.get("TESTING"):
        raise RuntimeError("Fixtures can only be created in an explicitly injected test app")
    root = Path(app.config["UPLOAD_FOLDER"]).resolve()
    real_root = Path(__file__).with_name("uploads").resolve()
    if root == real_root or real_root in root.parents or root in real_root.parents:
        raise RuntimeError("Refusing to write fixtures to business uploads")
    run_id = run_id or uuid.uuid4().hex
    users = {}
    user_models = {}
    for role, password in PASSWORDS.items():
        user = User(username=f"qa_{role}_{run_id[:8]}", nickname=f"QA {role}",
                    password_hash=_password_hash(password), role="admin" if role == "admin" else "user",
                    balance=Decimal("300.00") if role in {"buyer", "legacy"} else Decimal("0.00"),
                    frozen_balance=Decimal("0.00"), token_version=0)
        db.session.add(user)
        user_models[role] = user
    db.session.flush()
    for role, user in user_models.items():
        users[role] = {"id": user.id, "username": user.username, "password": PASSWORDS[role]}
    if db.session.get(RateLimitGuard, 1) is None:
        db.session.add(RateLimitGuard(id=1))
    author_id = user_models["author"].id
    assets = {}
    for key, purpose, content, extension in [
        ("cover", "image", SVG, "svg"), ("resource", "resource", RESOURCE, "txt"),
        ("free", "asset", SVG, "svg"), ("preview", "asset", SVG, "svg"),
        ("locked", "asset", SVG, "svg"),
    ]:
        directory = root / purpose
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}_{key}.{extension}"
        (directory / name).write_bytes(content)
        asset = FileAsset(stored_name=name, owner_id=author_id, purpose=purpose, size=len(content),
                          sha256=hashlib.sha256(content).hexdigest(), state="active", is_public=purpose == "image")
        db.session.add(asset)
        assets[key] = asset
    db.session.flush()
    definitions = {
        "free": (0, "published", False, [("Free first", False, "free"), ("Free second", False, None)]),
        "paid": (100, "published", False, [("Paid preview", True, "preview"), ("Paid locked", False, "locked")]),
        "paidNoPreview": (100, "published", False, [("No preview", False, "locked")]),
        "empty": (0, "published", False, []),
        "featured": (0, "published", True, [("Featured lesson", False, None)]),
        "draft": (100, "draft", False, [("Draft lesson", False, None)]),
        "offline": (100, "offline", False, [("Offline lesson", False, None)]),
    }
    toolkit_models = {}
    lesson_models = {}
    for key, (price, status, featured, lessons) in definitions.items():
        toolkit = Toolkit(title=f"QA {key} {run_id[:8]}", slug=f"qa-{key}-{run_id}",
                          summary="Isolated QA fixture", description="QA description: **safe Markdown**.",
                          category="生信分析", level="入门", cover=f"/api/uploads/{assets['cover'].stored_name}",
                          price=Decimal(str(price)), author_id=author_id, status=status, featured=featured,
                          tags="QA", resource_name="fixture.txt", resource_path=assets["resource"].stored_name)
        db.session.add(toolkit)
        db.session.flush()
        toolkit_models[key] = toolkit
        for position, (title, preview, image_key) in enumerate(lessons):
            lesson = Lesson(toolkit_id=toolkit.id, title=title, content=f"## {title}\n\nOnly an isolated fixture.",
                            duration=5, sort_order=position, is_preview=preview,
                            image=f"file:{assets[image_key].stored_name}" if image_key else None)
            db.session.add(lesson)
            db.session.flush()
            lesson_models[(key, position)] = lesson
            if image_key:
                db.session.add(FileReference(asset_id=assets[image_key].id, toolkit_id=toolkit.id,
                                             lesson_id=lesson.id, usage="lesson", binding_key=f"lesson:{lesson.id}:image"))
        for usage, asset_key in [("cover", "cover"), ("resource", "resource")]:
            db.session.add(FileReference(asset_id=assets[asset_key].id, toolkit_id=toolkit.id,
                                         usage=usage, binding_key=f"toolkit:{toolkit.id}:{usage}"))
    db.session.commit()
    return {"runId": run_id, "users": users,
            "toolkits": {key: value.id for key, value in toolkit_models.items()},
            "lessons": {"freeFirst": lesson_models[("free", 0)].id,
                        "freeSecond": lesson_models[("free", 1)].id,
                        "paidPreview": lesson_models[("paid", 0)].id,
                        "paidLocked": lesson_models[("paid", 1)].id,
                        "paidNoPreview": lesson_models[("paidNoPreview", 0)].id},
            "assets": {key: value.stored_name for key, value in assets.items()}}


def login_headers(client, user):
    response = client.post("/api/auth/login", json={"username": user["username"], "password": user["password"]})
    if response.status_code != 200:
        raise AssertionError(f"Fixture login failed: {response.status_code} {response.get_json()}")
    return {"Authorization": f"Bearer {response.get_json()['token']}"}
