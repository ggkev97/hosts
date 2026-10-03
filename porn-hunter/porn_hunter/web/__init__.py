"""Browser UI: search, queue management and job control. Local-first and locked down:
loopback by default, Host-header and CSRF checks, optional token login, strict CSP."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import signal
import socket
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from croniter import croniter
from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file, session,
                   url_for)
from werkzeug.serving import make_server

from porn_hunter.app import App
from porn_hunter.errors import ConfigError, HunterError
from porn_hunter.scheduler import build_scheduler
from porn_hunter.web.jobs import JobBusy, JobManager
from porn_hunter.web.security import LOOPBACK_HOSTS, host_name, safe_url

log = logging.getLogger(__name__)

MAX_IDS = 500
MAX_QUERIES = 20


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def result_dict(r) -> dict:
    v = r.video
    return {"id": v.id, "score": round(r.score, 4), "site": v.site, "title": v.title, "url": safe_url(v.url),
            "duration": v.duration, "status": v.dl_status, "thumb": f"/thumb/{v.id}"}


def tail(path: Path, lines: int = 80, max_bytes: int = 65536) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - max_bytes))
            return "\n".join(fh.read().decode("utf-8", "replace").splitlines()[-lines:])
    except OSError:
        return ""


def create_app(hunter: App, token: str | None = None, jobs: JobManager | None = None) -> Flask:
    cfg = hunter.cfg
    web = cfg.web
    jobs = jobs or JobManager(cfg.path("lock_file"))
    flask_app = Flask(__name__)
    flask_app.config.update(
        SECRET_KEY=(hmac.new(token.encode(), b"porn-hunter-session", hashlib.sha256).hexdigest()
                    if token else secrets.token_hex(32)),
        SESSION_COOKIE_SECURE=web.secure_cookies, SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_NAME="ph_session",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7), MAX_CONTENT_LENGTH=64 * 1024)
    flask_app.extensions["jobs"] = jobs
    flask_app.jinja_env.filters["safe_url"] = safe_url
    failures: list[float] = []          # recent failed logins (throttle)

    # ---- guards ---------------------------------------------------------------------------
    def bearer_ok() -> bool:
        """Scripts may authenticate with `Authorization: Bearer <token>` instead of a cookie session."""
        header = request.headers.get("Authorization", "")
        return bool(token) and header.startswith("Bearer ") and hmac.compare_digest(header[7:], token)

    def authed() -> bool:
        return not token or session.get("auth") is True or bearer_ok()

    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @flask_app.before_request
    def guard():
        # DNS-rebinding defence: with no token, only accept loopback (or explicitly allowed) Host names.
        if not token and host_name(request.host) not in LOOPBACK_HOSTS | set(web.allowed_hosts):
            abort(400, "unexpected Host header")
        if request.endpoint in ("static", "login"):
            pass
        elif not authed():
            if request.path.startswith("/api/"):
                return jsonify(error="authentication required"), 401
            return redirect(url_for("login", next=request.path))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            # Bearer requests carry no ambient cookie credentials, so they are not CSRF-able.
            if not bearer_ok() and not hmac.compare_digest(sent, session.get("csrf", "\0")):
                if request.path.startswith("/api/"):
                    return jsonify(error="missing or invalid CSRF token"), 403
                abort(403, "missing or invalid CSRF token")

    @flask_app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; manifest-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.endpoint not in ("thumb", "static", "media"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @flask_app.context_processor
    def inject():
        return {"csrf_token": csrf_token(), "blur_default": web.blur_thumbnails,
                "sites": list(cfg.sites), "auth_enabled": bool(token)}

    # ---- helpers --------------------------------------------------------------------------
    def run_search(args) -> tuple[list[dict], str | None]:
        query = (args.get("q") or "").strip()
        if not query:
            return [], None
        k = _int(args.get("k"), web.results_per_page, 1, 200)
        chosen = [s for s in args.getlist("site") if s in cfg.sites] or None
        try:
            results = hunter.searcher.search(query, k, _float(args.get("min_score")), chosen,
                                             args.get("new_only") == "1")
            return [result_dict(r) for r in results], None
        except HunterError as exc:
            return [], str(exc)
        except Exception:
            log.exception("search failed")
            return [], "Search failed. If this is the first run, the CLIP model may not have loaded; see the log."

    def ids_from(body) -> list[int]:
        ids = body.get("ids") if isinstance(body, dict) else None
        if (not isinstance(ids, list) or not ids or len(ids) > MAX_IDS
                or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
            abort(400, description=f"'ids' must be a list of 1-{MAX_IDS} integers")
        return ids

    def json_or_html_error(exc):
        if request.path.startswith("/api/"):
            return jsonify(error=exc.description), exc.code
        return render_template("error.html", code=exc.code, message=exc.description), exc.code

    for code in (400, 403, 404, 405, 413):
        flask_app.register_error_handler(code, json_or_html_error)

    def start_job(name, fn):
        try:
            job = jobs.start(name, fn)
        except JobBusy:
            return jsonify(error="a job is already running"), 409
        return jsonify(job=job.to_dict()), 202

    def next_runs() -> list[dict]:
        out = []
        for name in ("index", "download"):
            expr = getattr(cfg.scheduler, f"{name}_cron")
            out.append({"job": name, "cron": expr,
                        "next": croniter(expr, datetime.now()).get_next(datetime).strftime("%Y-%m-%d %H:%M")})
        return out

    # ---- auth -----------------------------------------------------------------------------
    @flask_app.route("/login", methods=["GET", "POST"])
    def login():
        if not token:
            return redirect(url_for("search_page"))
        error = None
        if request.method == "POST":
            now = time.time()
            failures[:] = [t for t in failures if now - t < 60]
            if len(failures) >= 5:
                abort(429, "too many failed attempts; wait a minute")
            if hmac.compare_digest(request.form.get("token", ""), token):
                session.clear()
                session["auth"] = True
                session.permanent = True
                dest = request.args.get("next", "")
                return redirect(dest if dest.startswith("/") and not dest.startswith("//") else url_for("search_page"))
            failures.append(now)
            error = "Wrong token."
        return render_template("login.html", error=error)

    @flask_app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @flask_app.errorhandler(429)
    def too_many(exc):
        return render_template("error.html", code=429, message=exc.description), 429

    # ---- pages ----------------------------------------------------------------------------
    @flask_app.get("/")
    def search_page():
        results, error = run_search(request.args)
        stats = hunter.store.stats()
        return render_template(
            "search.html", q=(request.args.get("q") or "").strip(), results=results, error=error,
            indexed=stats["embedded"], k=_int(request.args.get("k"), web.results_per_page, 1, 200),
            chosen_sites=request.args.getlist("site"), new_only=request.args.get("new_only") == "1",
            min_score=request.args.get("min_score", ""))

    @flask_app.get("/queue")
    def queue_page():
        store = hunter.store
        return render_template("queue.html", queued=store.queued(), failed=store.list_status("failed"),
                               downloaded=store.list_status("downloaded", 50))

    @flask_app.get("/status")
    def status_page():
        return render_template("status.html", stats=hunter.store.stats(), schedule=next_runs(),
                               default_queries="\n".join(cfg.index.queries), pages=cfg.index.pages_per_query,
                               scheduler_in_web=web.run_scheduler, log_tail=tail(cfg.path("log_file")),
                               output_dir=str(cfg.path("output_dir")))

    @flask_app.get("/favicon.ico")
    def favicon():
        return "", 204

    @flask_app.get("/thumb/<int:vid>")
    def thumb(vid):
        video = hunter.store.get(vid)
        if video is None or not video.thumb_path:
            abort(404)
        path, root = Path(video.thumb_path).resolve(), cfg.path("thumbs_dir").resolve()
        if root not in path.parents or not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/jpeg", max_age=86400)

    def downloaded_file(vid: int):
        """The video's downloaded file, only if it really lives inside the output directory."""
        video = hunter.store.get(vid)
        if video is None or video.dl_status != "downloaded" or not video.dl_path:
            abort(404)
        path, root = Path(video.dl_path).resolve(), cfg.path("output_dir").resolve()
        if root not in path.parents or not path.is_file():
            abort(404)
        return video, path

    @flask_app.get("/watch/<int:vid>")
    def watch(vid):
        video, path = downloaded_file(vid)
        return render_template("watch.html", video=video, ext=path.suffix.lower())

    @flask_app.get("/media/<int:vid>")
    def media(vid):
        """Stream a downloaded video. Range requests are supported (iOS Safari requires them)."""
        _, path = downloaded_file(vid)
        return send_file(path, conditional=True, max_age=0)

    # ---- JSON API ---------------------------------------------------------------------------
    @flask_app.get("/api/search")
    def api_search():
        results, error = run_search(request.args)
        if error:
            return jsonify(error=error), 400
        return jsonify(results=results)

    @flask_app.get("/api/status")
    def api_status():
        return jsonify(stats=hunter.store.stats(), schedule=next_runs(), **jobs.snapshot())

    @flask_app.get("/api/jobs")
    def api_jobs():
        return jsonify(**jobs.snapshot())

    @flask_app.post("/api/queue")
    def api_queue():
        ids = ids_from(request.get_json(silent=True))
        return jsonify(queued=hunter.store.queue(ids))

    @flask_app.post("/api/queue/remove")
    def api_queue_remove():
        body = request.get_json(silent=True) or {}
        if body.get("all") is True:
            return jsonify(removed=hunter.store.dequeue())
        return jsonify(removed=hunter.store.dequeue(ids_from(body)))

    @flask_app.post("/api/download")
    def api_download():
        """Queue the given videos and download exactly those, in the background."""
        ids = ids_from(request.get_json(silent=True))
        store = hunter.store
        known = [v for v in (store.get(i) for i in ids) if v is not None]
        if not known:
            abort(400, description="no such videos")
        if jobs.snapshot()["running"]:
            return jsonify(error="a job is already running"), 409
        store.queue(v.id for v in known)
        todo = [v for v in (store.get(v.id) for v in known) if v.dl_status == "queued"]
        if not todo:
            return jsonify(error="nothing to download (already downloaded)"), 400
        return start_job("download", lambda: hunter.downloader.run_queue(videos=todo).summary())

    @flask_app.post("/api/jobs/download")
    def api_job_download():
        return start_job("download", lambda: hunter.downloader.run_queue().summary())

    @flask_app.post("/api/jobs/index")
    def api_job_index():
        body = request.get_json(silent=True) or {}
        queries = body.get("queries") or cfg.index.queries
        if (not isinstance(queries, list) or not queries or len(queries) > MAX_QUERIES
                or not all(isinstance(q, str) and 0 < len(q.strip()) <= 200 for q in queries)):
            abort(400, description=f"'queries' must be 1-{MAX_QUERIES} non-empty strings (max 200 chars)")
        sites = body.get("sites") or None
        if sites is not None and (not isinstance(sites, list) or not set(sites) <= set(cfg.enabled_sites())):
            abort(400, description="unknown or disabled site")
        pages = _int(body.get("pages"), cfg.index.pages_per_query, 1, 20)
        queries = [q.strip() for q in queries]
        return start_job("index", lambda: hunter.indexer(sites).run(queries, sites, pages).summary())

    return flask_app


def lan_urls(port: int) -> list[str]:
    """Best-effort URL(s) other devices on the local network can use to reach this machine."""
    urls = []
    for target in ("10.255.255.255", "192.168.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((target, 1))                  # no packet is sent; just selects a route
                ip = sock.getsockname()[0]
        except OSError:
            continue
        url = f"http://{ip}:{port}/"
        if not ip.startswith("127.") and url not in urls:
            urls.append(url)
    return urls


def warm_up(hunter: App, background: bool = False) -> threading.Thread | None:
    """Build everything up front so the first search isn't slow. A model that can't load (e.g.
    offline) is not fatal: search reports it, other pages still work. With background=True the
    slow part (model + index) loads in a thread so the server can start listening immediately."""
    hunter.store, hunter.http, hunter.downloader  # noqa: B018

    def load():
        try:
            log.info("loading the CLIP model and index (the first run downloads the weights)...")
            hunter.vindex, hunter.searcher  # noqa: B018
            log.info("model and index ready")
        except Exception as exc:
            log.warning("model/index not ready, search will fail until it is: %s", exc)

    if not background:
        load()
        return None
    thread = threading.Thread(target=load, name="warm-up", daemon=True)
    thread.start()
    return thread


def serve(hunter: App, host: str | None = None, port: int | None = None, token: str | None = None,
          with_scheduler: bool | None = None, on_ready=None) -> None:
    web = hunter.cfg.web
    host = web.host if host is None else host
    port = web.port if port is None else port
    token = token or os.environ.get("PORN_HUNTER_WEB_TOKEN") or web.auth_token
    if host not in LOOPBACK_HOSTS and not token:
        raise ConfigError(f"refusing to listen on {host!r} without an auth token: set web.auth_token or "
                          "PORN_HUNTER_WEB_TOKEN (and put TLS in front of it, e.g. a reverse proxy)")
    if token and len(token) < 12:
        raise ConfigError("the web auth token must be at least 12 characters")
    if host not in LOOPBACK_HOSTS:
        log.warning("listening on %s over plain HTTP: the token is sent unencrypted; use TLS in front", host)

    warm_up(hunter, background=True)
    flask_app = create_app(hunter, token)
    server = make_server(host, port, flask_app, threaded=True)
    stop = threading.Event()
    sched_thread = None
    if web.run_scheduler if with_scheduler is None else with_scheduler:
        sched = build_scheduler(hunter, stop)
        run_now = [n for n, flag in (("index", hunter.cfg.scheduler.run_index_on_start),
                                     ("download", hunter.cfg.scheduler.run_download_on_start)) if flag]
        sched_thread = threading.Thread(target=sched.run_forever, args=(run_now,), name="scheduler", daemon=True)
        sched_thread.start()
        log.info("scheduler running inside the web process")

    def shutdown(signum=None, frame=None):
        log.warning("shutting down")
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
    log.info("web UI on http://%s:%d/ (%s)", host if ":" not in host else f"[{host}]", server.server_port,
             "token required" if token else "no login: loopback only")
    if host in ("0.0.0.0", "::"):
        for url in lan_urls(server.server_port):
            log.info("on your phone (same Wi-Fi) open %s", url)
    if on_ready:
        on_ready(server, stop)
    try:
        server.serve_forever()
    finally:
        stop.set()
        flask_app.extensions["jobs"].wait(5)
        server.server_close()
        if sched_thread:
            sched_thread.join(5)
        log.info("web UI stopped")
