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
from cloudextractor.catalog import Catalog
from cloudextractor.dashboard_jobs import Jobs
from cloudextractor.dashboard_media import Gallery, VIDEOS, jpeg_preview
from cloudextractor.download_dates import DateRange


PAGE_SIZES = (20, 50, 100)
GROUP_INTERVALS = (2, 5, 10, 30)
ACTIVE = ("running", "scanning", "stopping")


def paginate(paths: list[str]) -> tuple[list[str], int, int, int]:
    size = request.args.get("page_size", default=50, type=int)
    if size not in PAGE_SIZES:
        abort(400)
    pages = max(1, (len(paths) + size - 1) // size)
    page = min(pages, max(1, request.args.get("page", default=1, type=int)))
    start = (page - 1) * size
    return paths[start:start + size], page, pages, size


def group_interval() -> int:
    interval = request.args.get("group_interval", default=0, type=int)
    if interval not in (0, *GROUP_INTERVALS):
        abort(400)
    return interval


def create_app(archive: Path, state: Path) -> Flask:
    app = Flask(__name__, static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 4096
    cache = state / ".dashboard"
    if cache.is_symlink():
        raise ValueError("Use a real dashboard state directory.")
    cache.mkdir(mode=0o700, exist_ok=True)
    jobs = Jobs(cache)
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
        job, scan, usage = jobs.snapshot(account)
        ready = archive_ready()
        media = request.args.get("media", "both")
        if media not in ("image", "video", "both"):
            abort(400)
        folder = request.args.get("folder", "")
        local = gallery.items(account, job["status"] in ACTIVE) if ready else None
        cloud = scan.catalog if scan and scan.complete else None
        downloaded = local.filtered(media, folder) if local else []
        pending = [path for path in scan.catalog.filtered(media, folder) if path not in local.files] if scan and local else []
        view = request.args.get("view", "downloaded")
        paths = pending if view == "pending" else downloaded
        catalog = scan.catalog if view == "pending" and scan else local
        interval = group_interval()
        groups = {}
        if interval and view == "downloaded" and media != "video" and local:
            paths, groups = local.grouped(paths, interval)
        visible, page, pages, page_size = paginate(paths)
        local_folders = local.folders if local else {}
        cloud_folders = cloud.folders if cloud else {}
        cloud_summary = cloud.summary(media, folder) if cloud else None
        if usage:
            cloud_summary = {"bytes": usage["bytes"], "filtered": cloud_summary["filtered"] if cloud_summary else None,
                             "reported_at": usage["time"]}
        return jsonify(
            job=job, ready=ready, downloaded=len(downloaded), pending=len(pending) if scan and ready else None,
            scan={"time": scan.time, "limit": scan.limit, "unmeasured": scan.unmeasured, "complete": scan.complete} if scan else None,
            local=local.summary(media, folder) if local else None,
            cloud=cloud_summary,
            folders=[
                {"name": name,
                 "local": Catalog.filtered_size(local_folders.get(name, {}), media) if local else None,
                 "cloud": Catalog.filtered_size(cloud_folders.get(name, {}), media) if cloud else None}
                for name in sorted(local_folders.keys() | cloud_folders.keys(), reverse=True)
            ],
            paths=visible, page=page, pages=pages, page_size=page_size, total=len(paths),
            sizes={path: catalog.files[path] for path in visible},
            types={path: catalog.types[path] for path in visible},
            groups={path: {"count": len(groups[path]), "bytes": sum(local.files[member] for member in groups[path])}
                    for path in visible if path in groups},
        )

    @app.get("/api/group")
    def group():
        account = selected_account(request.args.get("account", ""))
        interval = group_interval()
        if not interval or not archive_ready():
            abort(404)
        folder = request.args.get("folder", "")
        job, _, _ = jobs.snapshot(account)
        local = gallery.items(account, job["status"] in ACTIVE)
        _, groups = local.grouped(local.filtered("image", folder), interval)
        path = request.args.get("path", "")
        members = groups.get(path)
        if not members:
            members = next((group for group in groups.values() if path in group), None)
        if not members:
            abort(404)
        visible, page, pages, _ = paginate(members)
        return jsonify(
            paths=visible, sizes={path: local.files[path] for path in visible},
            page=page, pages=pages, count=len(members),
            newest=local.timestamps[members[0]], oldest=local.timestamps[members[-1]],
        )

    @app.get("/api/jobs")
    def job_status():
        account = selected_account(request.args.get("account", ""))
        return jsonify(job=jobs.status(account), ready=archive_ready())

    @app.post("/api/jobs")
    def control():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        account = selected_account(data.get("account", ""))
        action = data.get("action")
        raw_dates = data.get("date_range")
        limit = data.get("limit", default_limit if raw_dates is None else None)
        if action not in ("run", "scan", "stop") or (limit is not None and (type(limit) is not int or limit < 1)):
            abort(400)

        date_range = None
        if raw_dates is not None:
            if action != "run" or limit is not None or not isinstance(raw_dates, dict):
                return jsonify(error="Wybierz zakres dat albo liczbę pozycji."), 400
            try:
                date_range = DateRange(raw_dates.get("start"), raw_dates.get("end"), raw_dates.get("timezone"))
                date_range.utc_bounds()
            except ValueError as error:
                return jsonify(error=str(error)), 400

        try:
            if action == "stop":
                jobs.stop(account)
            elif action == "scan":
                jobs.start(account, action, None)
            elif archive_ready():
                jobs.start(account, action, limit, date_range)
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
