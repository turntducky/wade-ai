from __future__ import annotations

import asyncio
import logging

from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.agents.monitors.base import MonitorDaemon
from app.core.events import EventType, WadeEvent, InternalEventBus

logger = logging.getLogger("wade.monitors.build_logs")

class BuildLogMonitor(MonitorDaemon):
    """Watches workspace for compilation/build logs and errors, emitting them to the Event Bus."""
    name = "build_logs"

    def __init__(self, event_bus: InternalEventBus, watch_dir: Path) -> None:
        super().__init__(event_bus)
        self._watch_dir = watch_dir
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._observer = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        handler = _LogEventHandler(self._queue_event)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watch_dir), recursive=True)
        self._observer.start()
        logger.info("[BUILD_LOGS] Sensor started. Watching %s for build errors.", self._watch_dir)
        try:
            while True:
                path_str, event_type = await self._event_queue.get()
                await self._process_log(path_str, event_type)
        finally:
            self._observer.stop()
            self._observer.join()

    def _queue_event(self, path_str: str, event_type: str) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._event_queue.put_nowait, (path_str, event_type))

    async def _process_log(self, path_str: str, event_type: str) -> None:
        path = Path(path_str)
        if path.suffix not in {".log", ".out", ".err", ".txt"}:
            return
        
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                tail = "".join(lines[-20:]).lower()
                if "error" in tail or "exception" in tail or "failed" in tail or "traceback" in tail:
                    await self.emit(WadeEvent(
                        event_type=EventType.BUILD_LOG,
                        payload={"path": path_str, "tail": tail, "event_type": event_type},
                        source="monitor:build_logs",
                    ))
                    logger.info("[BUILD_LOGS] Detected compilation/build error in %s", path.name)
        except Exception:
            pass

class _LogEventHandler(FileSystemEventHandler):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._callback(event.src_path, "created")

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._callback(event.src_path, "modified")