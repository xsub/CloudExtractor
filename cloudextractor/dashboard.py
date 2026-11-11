from io import BytesIO
import os
from pathlib import Path
import signal
import subprocess
import sys

from flask import Flask, abort, jsonify, request, send_file
from PIL import Image
from waitress import serve as serve_http

from cloudextractor.adapters.icloud import account_name
from cloudextractor.dashboard_jobs import Jobs
from cloudextractor.dashboard_media import Gallery, VIDEOS, jpeg_preview


PAGE_SIZE = 48
ACTIVE = ("running", "scanning", "stopping")


def create_app(archive: Path, state: Path) -> Flask:
    app = Flask(__name__, static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 4096
    cache = state / ".dashboard"
    if cache.is_symlink():
        raise ValueError("Use a real dashboard state directory.")
    cache.mkdir(mode=0o700, exist_ok=True)
    jobs = Jobs(archive, cache)
    gallery = Gallery(archive)
    app.extensions["cloudextractor_jobs"] = jobs
    configured_limit = os.environ.get("ICLOUD_RECENT", "").strip()
    default_limit = int(configured_limit) if configured_limit.isascii() and configured_limit.isdecimal() else 20

    def accounts() -> list[str]:
        names = set()
        candidates = [os.environ.get("ICLOUD_USERNAME", "")]
        for root in (state, archive):
            if root.is_dir():
                candidates.extend(path.name for path in root.iterdir() if path.is_dir() and not path.is_symlink())
        for candidate in candidates:
            try:
                names.add(account_name(candidate))
            except ValueError:
                continue
        return sorted(names)

    def selected_account(value: str) -> str:
        if not isinstance(value, str) or value not in accounts():
            abort(404)
        return value

    def archive_ready() -> bool:
        marker = archive / ".cloudextractor-archive"
        return not marker.is_symlink() and marker.is_file()

    @app.before_request
    def check_origin():
        if request.host.split(":", 1)[0] not in ("localhost", "127.0.0.1"):
            abort(403)
        if request.method == "POST" and request.headers.get("X-CloudExtractor") != "1":
            abort(403)

    @app.after_request
    def protect(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; object-src 'none'"
        return response

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/accounts")
    def list_accounts():
        return jsonify(accounts=accounts(), default_limit=default_limit)

    @app.get("/api/state")
    def status():
        account = selected_account(request.args.get("account", ""))
        job, scan = jobs.snapshot(account)
        ready = archive_ready()
        downloaded, present = gallery.items(account, job["status"] in ACTIVE) if ready else ([], set())
        pending = [path for path in scan["paths"] if path not in present] if scan and ready else []
        view = request.args.get("view", "downloaded")
        paths = pending if view == "pending" else downloaded
        page = max(1, request.args.get("page", default=1, type=int))
        start = (page - 1) * PAGE_SIZE
        return jsonify(
            job=job, ready=ready, downloaded=len(downloaded), pending=len(pending) if scan and ready else None,
            scan={"time": scan["time"], "limit": scan["limit"]} if scan else None,
            paths=paths[start:start + PAGE_SIZE], page_size=PAGE_SIZE, total=len(paths),
        )

    @app.post("/api/jobs")
    def control():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        account = selected_account(data.get("account", ""))
        action = data.get("action")
        limit = data.get("limit", default_limit)
        if action not in ("run", "scan", "stop") or (limit is not None and (type(limit) is not int or limit < 1)):
            abort(400)
        try:
            if action == "stop":
                jobs.stop(account)
            elif archive_ready():
                jobs.start(account, action, limit)
            else:
                raise ValueError("Archiwum jest niedostępne. Sprawdź montowanie na hoście.")
        except ValueError as error:
            return jsonify(error=str(error)), 409
        except OSError:
            return jsonify(error="Nie udało się uruchomić procesu."), 500
        return jsonify(ok=True), 202

    @app.get("/api/media")
    def media():
        account = selected_account(request.args.get("account", ""))
        if not archive_ready():
            abort(404)
        try:
            path = gallery.media(account, request.args.get("path", ""))
            kind = request.args.get("kind", "preview")
            if kind == "original" or (kind == "preview" and path.suffix.lower() in VIDEOS):
                return send_file(path, as_attachment=kind == "original", conditional=True)
            size = 360 if kind == "thumb" else 2400
            image = jpeg_preview(path, path.stat().st_mtime_ns, size)
            if not image:
                abort(415)
            return send_file(BytesIO(image), mimetype="image/jpeg")
        except FileNotFoundError:
            abort(404)
        except (OSError, ValueError, Image.DecompressionBombError, subprocess.SubprocessError):
            abort(415)

    return app


def serve(archive: Path, state: Path) -> None:
    app = create_app(archive, state)
    signal.signal(signal.SIGTERM, lambda _signal, _frame: sys.exit(0))
    try:
        serve_http(app, host="0.0.0.0", port=8765, threads=8)
    finally:
        app.extensions["cloudextractor_jobs"].shutdown()
