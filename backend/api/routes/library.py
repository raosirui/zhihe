"""Favorites, ratings, author library, and lesson progress endpoints."""
from decimal import Decimal

from flask import jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import select
from sqlalchemy.orm import Session

from api import api
from api.support import _access_toolkit, _purchased, _serialize_list
from extensions import db
from models import (Favorite, Lesson, LessonProgress, Purchase, Toolkit, ToolkitRating, utcnow)
from security import (APIError, assert_identity, current_user, json_object, lock_existing_child,
                      positive_id, request_identity)
from access import can_learn, is_visible
import money_service as money


@api.post("/api/toolkits/<int:toolkit_id>/favorite")
@jwt_required()
def toggle_favorite(toolkit_id):
    identity = request_identity()
    with Session(db.engine) as session, session.begin():
        toolkit = money.lock_toolkit(session, toolkit_id)
        if not toolkit:
            raise APIError("工具包不存在", 404)
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        if not is_visible(toolkit, user, _purchased(session, user, toolkit_id)):
            raise APIError("工具包不存在", 404)
        favorite = session.execute(select(Favorite).where(Favorite.user_id == user.id, Favorite.toolkit_id == toolkit_id)
                                   .with_for_update()).scalar_one_or_none()
        if favorite:
            session.delete(favorite)
            active = False
        else:
            session.add(Favorite(user_id=user.id, toolkit_id=toolkit_id))
            active = True
    return jsonify({"favorited": active})


@api.get("/api/me/favorites")
@jwt_required()
def favorites():
    user = current_user()
    items = db.session.execute(select(Toolkit).join(Favorite).where(Favorite.user_id == user.id)
                               .order_by(Favorite.created_at.desc())).scalars().all()
    bought = set(db.session.execute(select(Purchase.toolkit_id).where(Purchase.user_id == user.id)).scalars())
    return jsonify(_serialize_list([item for item in items if is_visible(item, user, item.id in bought)], user))


@api.put("/api/toolkits/<int:toolkit_id>/rating")
@jwt_required()
def rate_toolkit(toolkit_id):
    """Buyers rate a toolkit 1~5; re-rating overwrites. The aggregate is
    recomputed under the Toolkit lock, matching the money lock order."""
    score = json_object().get("score")
    if type(score) is not int or not 1 <= score <= 5:
        raise APIError("评分须为 1～5 的整数")
    identity = request_identity()
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        toolkit = money.lock_toolkit(session, toolkit_id)
        if not toolkit:
            raise APIError("工具包不存在", 404)
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        if not money.current_purchase(session, user.id, toolkit_id):
            raise APIError("购买后才能评分", 403, "NOT_PURCHASED")
        existing = lock_existing_child(session, ToolkitRating, ToolkitRating.id,
                                       ToolkitRating.user_id == user.id, ToolkitRating.toolkit_id == toolkit_id)
        if existing:
            existing.score = score
        else:
            session.add(ToolkitRating(user_id=user.id, toolkit_id=toolkit_id, score=score))
        session.flush()
        stats = session.execute(select(db.func.count(ToolkitRating.id), db.func.avg(ToolkitRating.score))
                                .where(ToolkitRating.toolkit_id == toolkit_id)).one()
        toolkit.rating_count = int(stats[0] or 0)
        toolkit.rating = Decimal(str(round(float(stats[1] or 5.0), 1))) if stats[0] else Decimal("5.0")
        result = {"rating": float(toolkit.rating), "ratingCount": toolkit.rating_count, "myScore": score}
    return jsonify(result)


@api.get("/api/me/toolkits")
@jwt_required()
def my_toolkits():
    user = current_user()
    return jsonify(_serialize_list(Toolkit.query.filter_by(author_id=user.id).order_by(Toolkit.created_at.desc()).all(), user))


@api.post("/api/lessons/<int:lesson_id>/complete")
@jwt_required()
def complete_lesson(lesson_id):
    identity = request_identity()
    with Session(db.engine) as session, session.begin():
        toolkit_id = session.execute(select(Lesson.toolkit_id).where(Lesson.id == lesson_id)).scalar_one_or_none()
        if toolkit_id is None:
            raise APIError("步骤不存在", 404)
        toolkit = money.lock_toolkit(session, toolkit_id)
        user = money.lock_users(session, [identity.uid])[identity.uid]
        assert_identity(session, user, identity)
        lesson = session.execute(select(Lesson).where(Lesson.id == lesson_id).with_for_update()
                                 .execution_options(populate_existing=True)).scalar_one_or_none()
        if not lesson or not toolkit or lesson.toolkit_id != toolkit_id:
            raise APIError("步骤不存在", 404)
        purchase = money.current_purchase(session, user.id, toolkit_id) is not None
        if not is_visible(toolkit, user, purchase):
            raise APIError("步骤不存在", 404)
        if not can_learn(toolkit, user, purchase, lesson):
            raise APIError("购买后方可完成此步骤", 403)
        dialect = session.get_bind().dialect.name
        values = {"user_id": user.id, "lesson_id": lesson_id, "created_at": utcnow()}
        if dialect == "mysql":
            from sqlalchemy.dialects.mysql import insert
            statement = insert(LessonProgress).values(**values)
            session.execute(statement.on_duplicate_key_update(user_id=statement.inserted.user_id))
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
            session.execute(insert(LessonProgress).values(**values).on_conflict_do_nothing(index_elements=["user_id", "lesson_id"]))
        else:
            raise APIError("数据服务不支持当前数据库", 503)
    return jsonify({"completed": True})


@api.get("/api/me/lesson-progress")
@jwt_required()
def lesson_progress():
    user = current_user()
    query = select(LessonProgress.lesson_id).where(LessonProgress.user_id == user.id)
    value = request.args.get("toolkit_id")
    if value is not None:
        try:
            toolkit_id = positive_id(int(value), "工具包ID")
        except ValueError:
            raise APIError("工具包ID无效") from None
        _access_toolkit(db.session, toolkit_id, user)
        query = query.join(Lesson).where(Lesson.toolkit_id == toolkit_id)
    return jsonify(list(db.session.execute(query).scalars()))
