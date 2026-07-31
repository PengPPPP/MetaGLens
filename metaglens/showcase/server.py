"""Showcase HTTP server (stdlib only).

Security is the design. The site may bind ``0.0.0.0``, so the handler exposes a
deliberately tiny, read-only surface:

  GET  /                 the one-page site (static HTML)
  POST /api/run          enqueue a demo run for a *whitelisted route only*
  GET  /api/status?id=   poll a run by opaque id
  GET  /api/report?id=   serve that run's generated report.html
  GET  /api/monitor?id=  serve that run's monitor.html
  GET  /api/script?id=   serve one rendered stage script (fixed stage)

There is no endpoint that takes a filesystem path or a command. Artefacts are
reached only through a server-generated run id mapped to a managed temp dir, and
the only thing the server can run is the fixed stub demo. Ids are validated as
hex tokens; anything else is 400/404.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .jobs import JobManager
from .page import build_page

_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")   # only ids we could have minted
_MAX_BODY = 4096                            # a run request is tiny


class _ShowcaseServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, *, manager: JobManager) -> None:
        super().__init__(addr, handler)
        self.manager = manager
        self.page_html = build_page(static=False).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server_version = "MetaGLensShowcase/1.0"

    def log_message(self, *args) -> None:
        return

    # -- helpers ---------------------------------------------------------- #
    def _send(self, code: int, body: bytes, ctype: str,
              extra_headers: Optional[dict] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Read-only site; forbid embedding tricks and sniffing.
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _valid_id(self, query) -> Optional[str]:
        raw = (query.get("id", [""])[0] or "").strip()
        return raw if _ID_RE.match(raw) else None

    def _serve_run_file(self, job_attr: str, query) -> None:
        """Serve an artefact of a run, addressed only by opaque id."""
        job_id = self._valid_id(query)
        if not job_id:
            self._send(400, b"bad id", "text/plain; charset=utf-8")
            return
        job = self.server.manager.get(job_id)
        if job is None:
            self._send(404, b"unknown run", "text/plain; charset=utf-8")
            return
        path = getattr(job, job_attr, "")
        if not path:
            self._send(404, b"not available", "text/plain; charset=utf-8")
            return
        try:
            data = Path(path).read_bytes()
        except OSError:
            self._send(404, b"not available", "text/plain; charset=utf-8")
            return
        self._send(200, data, "text/html; charset=utf-8")

    # -- routing ---------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                self._send(200, self.server.page_html, "text/html; charset=utf-8")
            elif path == "/healthz":
                self._json({"ok": True})
            elif path == "/api/status":
                job_id = self._valid_id(query)
                if not job_id:
                    self._json({"error": "bad id"}, code=400); return
                job = self.server.manager.get(job_id)
                if job is None:
                    self._json({"error": "unknown run"}, code=404); return
                self._json(job.public())
            elif path == "/api/report":
                self._serve_run_file("report_path", query)
            elif path == "/api/monitor":
                self._serve_run_file("monitor_path", query)
            elif path == "/api/script":
                job_id = self._valid_id(query)
                if not job_id:
                    self._send(400, b"bad id", "text/plain; charset=utf-8"); return
                job = self.server.manager.get(job_id)
                text = (job.script_text if job else "") or ""
                self._send(200, text.encode("utf-8"),
                           "text/plain; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:  # never leak a traceback
            self._json({"error": str(exc)}, code=500)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/run", "/api/attack"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > _MAX_BODY:
            self._json({"ok": False, "error": "payload too large"}, code=413)
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._json({"ok": False, "error": "invalid JSON"}, code=400)
            return

        if parsed.path == "/api/attack":
            # Runs the REAL repair boundary check on the probe. Safe: the check
            # only inspects and raises, it never executes anything.
            from .attacks import evaluate
            result = evaluate(payload.get("op", ""), payload.get("stage", ""),
                              payload.get("changes", {}))
            self._json(result)
            return

        # /api/run — the only thing a request may choose is a route name,
        # validated against a whitelist inside the manager. Never a command.
        route = str(payload.get("route", "")).strip()
        result = self.server.manager.submit(route)
        self._json(result, code=200 if result.get("ok") else 429)


def build_app(manager: Optional[JobManager] = None, host: str = "127.0.0.1",
              port: int = 0) -> _ShowcaseServer:
    """Construct the server bound to (host, port). Port 0 lets the OS choose."""
    manager = manager or JobManager()
    return _ShowcaseServer((host, port), _Handler, manager=manager)


def serve(host: str = "0.0.0.0", port: int = 8080,
          open_browser: bool = False) -> None:
    """Run the showcase server (blocking). Ctrl-C stops it."""
    manager = JobManager()
    server = build_app(manager=manager, host=host, port=port)
    actual_port = server.server_address[1]
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown_host}:{actual_port}/"
    print(f"MetaGLens showcase: {url}")
    if host in ("0.0.0.0", "::"):
        print(f"(bound on {host}:{actual_port} — reachable from other hosts; "
              f"read-only demo endpoints only)")
    print("Ctrl-C to stop.")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while True:
            thread.join(0.5)
    except KeyboardInterrupt:
        print("\nStopping showcase.")
    finally:
        server.shutdown()
        server.server_close()
        manager.shutdown()


def export_static(out_dir: str, route: str = "mag_per_sample") -> Path:
    """Export a backend-free static site: one pre-run demo baked in.

    Produces index.html (static mode), report.html, monitor.html and script.txt,
    so the whole story is viewable with no server at all.
    """
    from ..demo import run_demo
    import tempfile

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    tmp = tempfile.mkdtemp(prefix="metaglens_export_")
    try:
        result = run_demo(route, workdir=tmp)
        (out / "index.html").write_text(build_page(static=True), encoding="utf-8")
        report = result.get("report_html", "")
        monitor = result.get("monitor_html", "")
        if report and Path(report).is_file():
            (out / "report.html").write_bytes(Path(report).read_bytes())
        if monitor and Path(monitor).is_file():
            (out / "monitor.html").write_bytes(Path(monitor).read_bytes())
        from .. import routes
        from .jobs import SHOWCASE_SCRIPT_STAGE
        script = (Path(result.get("root", "")) / "work" / "metaglens_results"
                  / routes.STEPS[SHOWCASE_SCRIPT_STAGE].script)
        if script.is_file():
            (out / "script.txt").write_bytes(script.read_bytes())
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return out
