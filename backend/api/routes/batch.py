"""Batch package upload, progress, and status endpoints."""
import json
import re

from flask import current_app, jsonify, request, send_file
from flask_jwt_extended import jwt_required
from sqlalchemy import select
from sqlalchemy.orm import Session

from api import api
from extensions import db
from models import BatchJob
from security import APIError, client_ip, request_identity, rate_hits
from batch_kit import PROMPT_TEXT, build_skill_zip, build_template_zip
import batch_worker


@api.get("/api/batch/template")
def batch_template():
    return send_file(build_template_zip(), mimetype="application/zip", as_attachment=True, download_name="zhihe-standard-template.zip")


@api.get("/api/batch/skill")
def batch_skill():
    return send_file(build_skill_zip(), mimetype="application/zip", as_attachment=True, download_name="zhihe-toolkit-converter-skill.zip")


@api.get("/api/batch/prompt")
def batch_prompt():
    return jsonify({"text": PROMPT_TEXT})


@api.post("/api/batch/upload")
@jwt_required()
def batch_upload():
    """Accept a package, stream it to durable staging, and enqueue an async job.
    Returns immediately so Gunicorn's request timeout never kills a long import;
    clients poll /api/batch/jobs/<id> for per-item progress."""
    identity = request_identity()
    rate_hits([(f"batch:user:{identity.uid}", 10, 600), (f"batch:ip:{client_ip()}", 20, 600)])
    file = request.files.get("package")
    if not file:
        raise APIError("请选择要上传的 ZIP 文件")
    mode = request.form.get("mode", "skip")
    if mode not in {"skip", "update", "create"}:
        raise APIError("批量模式无效")
    publish = request.form.get("publish", "1").lower() not in {"0", "false", "no", "off"}
    job_id, path, size = batch_worker.stage_package(file.stream, current_app.config["MAX_CONTENT_LENGTH"])
    try:
        batch_worker.enqueue(identity, mode, publish, job_id, path, size)
    except Exception:
        batch_worker._safe_unlink(path)
        raise
    return jsonify({"jobId": job_id, "status": "queued", "mode": mode, "publish": publish,
                    "message": "已开始后台处理，可离开页面"}), 202


def _job_view(job, identity):
    if job.owner_id != identity.uid:
        raise APIError("任务不存在", 404)
    data = job.progress()
    data["results"] = json.loads(job.results_json or "[]")
    return data


@api.get("/api/batch/jobs/<job_id>")
@jwt_required()
def batch_job_status(job_id):
    identity = request_identity()
    if not re.fullmatch(r"[0-9a-f]{32}", job_id or ""):
        raise APIError("任务不存在", 404)
    with Session(db.engine) as session:
        job = session.get(BatchJob, job_id)
        if not job:
            raise APIError("任务不存在", 404)
        return jsonify(_job_view(job, identity))


@api.get("/api/batch/jobs")
@jwt_required()
def batch_job_list():
    identity = request_identity()
    with Session(db.engine) as session:
        jobs = session.execute(select(BatchJob).where(BatchJob.owner_id == identity.uid)
                               .order_by(BatchJob.created_at.desc()).limit(20)).scalars().all()
        return jsonify([job.progress() for job in jobs])
