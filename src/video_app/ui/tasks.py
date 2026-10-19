"""One background owner for execution, its asyncio loop and SQLite connection."""
from __future__ import annotations

import asyncio
from pathlib import Path
import queue
from threading import Event, Lock
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from ..core.engine import Engine
from ..core.pipeline import Pipeline
from ..core.production import VideoProduction
from ..core.workflow import WorkflowError


class TaskService(QThread):
    event = Signal(dict)

    def __init__(self, root: Path, parent=None, *, providers=None):
        super().__init__(parent)
        self.root = root
        self.commands = queue.Queue()
        self.stop_requested = Event()
        self.lock = Lock()
        self.pending = set()
        self.cancellations = {}
        self.current_project = None
        self.providers = providers

    def submit(self, project_id, *, retry=False, reconcile=False):
        with self.lock:
            if project_id in self.pending:
                return False
            self.pending.add(project_id)
            cancel = Event()
            self.cancellations[project_id] = cancel
        self.commands.put(("reconcile" if reconcile else "run", project_id, retry, cancel))
        return True

    def open_browser(self):
        return self.configure_browser()

    def submit_video(self, project_id, options):
        with self.lock:
            if project_id in self.pending:
                return False
            self.pending.add(project_id)
            cancel = Event()
            self.cancellations[project_id] = cancel
        self.commands.put(("video", project_id, dict(options), cancel))
        return True

    def configure_browser(self, settings=None, *, launch=True):
        request_id = uuid4().hex
        self.commands.put(("browser", None, {"settings": settings, "launch": launch, "request_id": request_id}, None))
        return request_id

    def cancel(self, project_id):
        with self.lock:
            cancel = self.cancellations.get(project_id)
            if cancel:
                cancel.set()

    def busy(self, project_id=None):
        with self.lock:
            return bool(self.pending) if project_id is None else project_id in self.pending

    def shutdown(self):
        self.stop_requested.set()
        with self.lock:
            for cancel in self.cancellations.values():
                cancel.set()

    def run(self):
        asyncio.run(self._loop())

    async def _loop(self):
        # GUI already performed startup recovery. Opening a worker connection
        # must not mark its own or another active run UNKNOWN.
        engine = Engine(self.root, recover=False)
        pipeline = Pipeline(engine, self.event.emit, self.providers)
        try:
            while not self.stop_requested.is_set():
                try:
                    command, project_id, retry, cancel = self.commands.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue
                self.current_project = project_id
                try:
                    if command == "browser":
                        result = await pipeline.open_browser(retry["settings"], launch=retry["launch"])
                        message = "连接已检查；本次未发送文稿。"
                        self.event.emit({"kind": "browser", "request_id": retry["request_id"], "message": message, "result": result})
                    elif not cancel.is_set():
                        if command == "video":
                            await VideoProduction(engine, self.event.emit).run(project_id, retry, cancel)
                        elif command == "reconcile":
                            await pipeline.reconcile(project_id, cancel)
                        else:
                            await pipeline.run(project_id, cancel, retry=retry)
                except Exception as error:
                    # Unexpected host errors are preserved, never surfaced as a
                    # successful node or silently replaced with local content.
                    code = getattr(error, "code", "RUNTIME_ERROR")
                    if project_id:
                        for step in engine.steps(project_id):
                            if step["state"] == "RUNNING" and step["active_run_id"]:
                                run = engine._one("SELECT dispatched FROM attempts WHERE id=?", (step["active_run_id"],))
                                state = "UNKNOWN" if run and run["dispatched"] else "FAILED"
                                engine.finish_run(step["active_run_id"], state, code, str(error))
                    self.event.emit({"kind": "browser" if command == "browser" else "error", "project_id": project_id,
                                     "request_id": retry["request_id"] if command == "browser" else None,
                                     "error": code, "message": f"{code} · {error}"})
                finally:
                    with self.lock:
                        self.pending.discard(project_id)
                        self.cancellations.pop(project_id, None)
                    self.current_project = None
                    if project_id:
                        self.event.emit({"kind": "idle", "project_id": project_id, "message": "本轮后台处理已结束，结果与状态已保存。"})
        finally:
            await pipeline.shutdown()
            engine.close()
