from __future__ import annotations

import asyncio
import logging

from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.agents.monitors.base import MonitorDaemon
from app.core.events import EventType, WadeEvent, InternalEventBus

logger = logging.getLogger("wade.monitors.filesystem")

_DEFAULT_WATCH_DIR = Path.home() / ".wade" / "workspace"

class FilesystemMonitor(MonitorDaemon):
    """Watches a directory and emits FS_CHANGE events onto the bus for each notable file change."""
    name = "filesystem"

    def __init__(self, event_bus: InternalEventBus, watch_dir: Path = _DEFAULT_WATCH_DIR) -> None:
        super().__init__(event_bus)
        self._watch_dir   = watch_dir
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._observer    = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        handler = _AsyncEventHandler(queue=self._event_queue, loop=self._loop)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watch_dir), recursive=False)
        self._observer.start()
        logger.info("[FILESYSTEM] Sensor started. Watching %s", self._watch_dir)
        try:
            while True:
                path_str, event_type = await self._event_queue.get()
                await self._on_fs_event(path_str, event_type)
        finally:
            self._observer.stop()
            self._observer.join()

    def change_watch_dir(self, new_path: Path) -> None:
        self._watch_dir = new_path
        
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join()

        if self._loop is None:
            logger.info("[FILESYSTEM] Watch dir changed to %s (monitor not running yet)", self._watch_dir)
            return

        handler = _AsyncEventHandler(queue=self._event_queue, loop=self._loop)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watch_dir), recursive=False)
        self._observer.start()
        logger.info("[FILESYSTEM] Watch dir changed to %s", self._watch_dir)

    async def _on_fs_event(self, path_str: str, event_type: str) -> None:
        path = Path(path_str)

        if path.name.startswith("."):
            return
        if path.suffix == ".md" and len(path.stem) == 8 and path.stem.replace("-", "").isdigit():
            return

        await self.emit(WadeEvent(
            event_type=EventType.FS_CHANGE,
            payload={"name": path.name, "path": str(path), "event_type": event_type},
            source="monitor:filesystem",
        ))
        logger.debug("[FILESYSTEM] Emitted fs.change: %s %s", event_type, path.name)

    def get_extra_status(self) -> dict:
        return {"watch_path": str(self._watch_dir)}


class _AsyncEventHandler(FileSystemEventHandler):
    """Bridge between watchdog's sync callbacks and asyncio."""
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._queue = queue
        self._loop  = loop

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, (event.src_path, "created"))

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, (event.src_path, "modified"))