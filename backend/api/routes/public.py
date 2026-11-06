"""Public API endpoints."""
from flask import jsonify

from api import api
from models import Toolkit, User


@api.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "zhihe-api"})


@api.get("/api/stats")
def public_stats():
    return jsonify({"toolkits": Toolkit.query.filter_by(status="published").count(), "users": User.query.count()})
