"""Serve only a built SPA; API errors must never turn into index.html."""
from pathlib import Path, PurePosixPath

from flask import abort, jsonify, send_from_directory


def _safe_file(root, relative):
    if not relative or "\\" in relative or "\x00" in relative:
        return None
    parts = relative.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        return None
    candidate = root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink():
            return None
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root):
        return None
    return candidate


def install_spa(app, dist_path=None):
    """Register build-only serving. An explicit path is useful for temp fixtures."""
    root = Path(dist_path) if dist_path is not None else Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise ValueError("The frontend build directory may not be a symlink")
    root = root.resolve()
    if _safe_file(root, "index.html") is None:
        raise ValueError("Frontend build is missing; run npm ci and npm run build first")

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def built_frontend(path):
        if path == "api" or path.startswith("api/"):
            return jsonify({"message": "接口或资源不存在"}), 404
        if path and ("\\" in path or any(part in {"", ".", ".."} or part.startswith(".") for part in path.split("/"))):
            abort(404)
        target = _safe_file(root, path) if path else None
        if target is not None:
            response = send_from_directory(root, path)
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return response
        # Missing scripts/images are 404s, not HTML. Only client-side routes
        # without a file suffix fall back to the build's index.
        if path and (PurePosixPath(path).suffix or path.startswith("assets/")):
            abort(404)
        response = send_from_directory(root, "index.html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    return app
