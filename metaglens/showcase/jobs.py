"""Bounded, single-concurrency job manager for showcase demo runs.

Judges may click "run" all at once, so the manager: accepts only a whitelisted
route (never a command), runs at most one demo at a time behind a bounded queue,
caps each run with a wall-clock timeout, keeps only the last few runs on disk,
and deletes the rest. A run id is an opaque server-generated token; nothing about
the filesystem is ever taken from a request.
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .. import routes

# Only these routes may be demoed. A request cannot ask for anything else.
DEMO_ROUTES = ("mag_per_sample", "contig_based")

# A representative stage whose rendered bash is shown as proof the output is a
# standalone, readable script. Whitelisted — never taken from the request.
SHOWCASE_SCRIPT_STAGE = "02_assembly"


@dataclass
class Job:
    job_id: str
    route: str
    status: str = "queued"          # queued | running | done | failed | timeout | busy
    submitted: float = field(default_factory=time.time)
    started: Optional[float] = None
    finished: Optional[float] = None
    stages: List[Dict[str, Any]] = field(default_factory=list)
    root: str = ""
    report_path: str = ""
    monitor_path: str = ""
    script_text: str = ""
    error: str = ""

    def public(self) -> Dict[str, Any]:
        """Only non-sensitive fields; never leaks a server filesystem path."""
        elapsed = None
        if self.started:
            elapsed = round((self.finished or time.time()) - self.started, 1)
        return {
            "id": self.job_id,
            "route": self.route,
            "status": self.status,
            "elapsed": elapsed,
            "stages": self.stages,
            "has_report": bool(self.report_path),
            "has_monitor": bool(self.monitor_path),
            "error": self.error,
            "note": "stub toolchain — no scientific results",
        }


class JobManager:
    """Runs stub demos one at a time, bounded and self-cleaning."""

    def __init__(self, queue_limit: int = 4, keep_runs: int = 6,
                 run_timeout: float = 60.0,
                 runner: Optional[Callable[..., Dict[str, Any]]] = None) -> None:
        self.queue_limit = queue_limit
        self.keep_runs = keep_runs
        self.run_timeout = run_timeout
        self._runner = runner            # injectable for tests
        self._jobs: Dict[str, Job] = {}
        self._order: List[str] = []      # submission order, for cleanup
        self._pending = 0                # queued + running
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._queue: List[str] = []
        self._worker = threading.Thread(target=self._run_loop, daemon=True)
        self._worker.start()

    # -- submission ------------------------------------------------------- #
    def submit(self, route: str) -> Dict[str, Any]:
        if route not in DEMO_ROUTES:
            return {"ok": False, "error": f"route not offered: {route}",
                    "status": "rejected"}
        with self._lock:
            if self._pending >= self.queue_limit:
                return {"ok": False, "status": "busy",
                        "error": "demo queue is full; please try again shortly"}
            job = Job(job_id=secrets.token_hex(8), route=route)
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._queue.append(job.job_id)
            self._pending += 1
            self._cv.notify()
            queued_ahead = self._pending - 1
        return {"ok": True, "id": job.job_id, "status": "queued",
                "queued_ahead": queued_ahead}

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    # -- worker ----------------------------------------------------------- #
    def _run_loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                job_id = self._queue.pop(0)
                job = self._jobs.get(job_id)
            if job is None:
                with self._lock:
                    self._pending = max(0, self._pending - 1)
                continue
            self._execute(job)
            with self._lock:
                self._pending = max(0, self._pending - 1)
            self._cleanup()

    def _execute(self, job: Job) -> None:
        job.status = "running"
        job.started = time.time()
        result_box: Dict[str, Any] = {}

        def target() -> None:
            runner = self._runner or self._default_runner
            try:
                result_box["result"] = runner(route=job.route)
            except Exception as exc:  # never let a demo crash the server
                result_box["error"] = str(exc)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(self.run_timeout)
        job.finished = time.time()

        if thread.is_alive():
            job.status = "timeout"
            job.error = f"demo exceeded {self.run_timeout:.0f}s"
            return
        if "error" in result_box:
            job.status = "failed"
            job.error = result_box["error"]
            return

        result = result_box.get("result") or {}
        job.stages = [{"step": s.get("step"), "status": s.get("status")}
                      for s in result.get("stages", [])]
        job.root = result.get("root", "")
        job.report_path = result.get("report_html", "")
        job.monitor_path = result.get("monitor_html", "")
        job.script_text = self._read_script(job.root)
        job.status = "done" if result.get("ok") else "failed"
        if not result.get("ok") and result.get("errors"):
            job.error = "; ".join(str(e) for e in result["errors"])[:400]

    def _default_runner(self, route: str) -> Dict[str, Any]:
        from ..demo import run_demo
        tmp = tempfile.mkdtemp(prefix="metaglens_showcase_")
        # keep=... irrelevant here: an explicit workdir disables auto-cleanup,
        # so outputs persist for serving and we delete them ourselves later.
        return run_demo(route, workdir=tmp)

    def _read_script(self, root: str) -> str:
        """Read one rendered stage script to prove the output is standalone."""
        if not root:
            return ""
        script_name = routes.STEPS[SHOWCASE_SCRIPT_STAGE].script
        path = Path(root) / "work" / "metaglens_results" / script_name
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # -- cleanup ---------------------------------------------------------- #
    def _cleanup(self) -> None:
        """Keep only the most recent runs; delete older temp trees."""
        with self._lock:
            finished = [jid for jid in self._order
                        if self._jobs.get(jid)
                        and self._jobs[jid].status in
                        ("done", "failed", "timeout")]
            stale = finished[:-self.keep_runs] if len(finished) > self.keep_runs \
                else []
        for jid in stale:
            job = self._jobs.get(jid)
            if job and job.root and job.root not in ("", "(cleaned up)"):
                shutil.rmtree(job.root, ignore_errors=True)
                job.root = "(cleaned up)"
                job.report_path = ""
                job.monitor_path = ""

    def shutdown(self) -> None:
        """Remove every managed temp directory (for tests / graceful stop)."""
        with self._lock:
            roots = [j.root for j in self._jobs.values()
                     if j.root and j.root not in ("", "(cleaned up)")]
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)
