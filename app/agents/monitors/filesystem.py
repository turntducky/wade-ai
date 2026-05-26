from __future__ import annotations

import asyncio
import logging

from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.agents.monitors.base import MonitorDaemon
from app.core.events import EventType, WadeEvent, InternalEventBus

logger = logging.getLogger("wade.monitors.filesystem")

_DEFAULT_WATCH_DIR  = Path.home() / ".wade" / "workspace"
_DEBOUNCE_SECONDS   = 5.0

_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".eggs", "*.egg-info",
}
_IGNORE_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".class",
    ".o", ".obj", ".swp", ".swo", ".DS_Store",
}
_IGNORE_NAME_PREFIXES = (".", "~", "#")


def _should_ignore(path: Path) -> bool:
    for part in path.parts:
        if part in _IGNORE_DIRS or part.endswith(".egg-info"):
            return True
    if path.name and path.name.startswith(_IGNORE_NAME_PREFIXES):
        return True
    if path.suffix in _IGNORE_SUFFIXES:
        return True
    # Dated markdown files (e.g. 05-26-26.md)
    if path.suffix == ".md" and len(path.stem) == 8 and path.stem.replace("-", "").isdigit():
        return True
    return False


class FilesystemMonitor(MonitorDaemon):
    """Watches a directory recursively with debouncing and emits FS_CHANGE events."""
    name = "filesystem"

    def __init__(
        self,
        event_bus: InternalEventBus,
        watch_dir: Path = _DEFAULT_WATCH_DIR,
        recursive: bool = True,
    ) -> None:
        super().__init__(event_bus)
        self._watch_dir   = watch_dir
        self._recursive   = recursive
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._observer    = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # path_str → (final_event_type, pending_task)
        self._debounce: dict[str, tuple[str, asyncio.Task]] = {}

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._start_observer()
        logger.info(
            "[FILESYSTEM] Sensor started. Watching %s (recursive=%s, debounce=%.0fs)",
            self._watch_dir, self._recursive, _DEBOUNCE_SECONDS,
        )
        try:
            while True:
                path_str, event_type = await self._event_queue.get()
                await self._schedule_debounced(path_str, event_type)
        finally:
            self._stop_observer()

    def _start_observer(self) -> None:
        handler = _AsyncEventHandler(queue=self._event_queue, loop=self._loop)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watch_dir), recursive=self._recursive)
        self._observer.start()

    def _stop_observer(self) -> None:
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join()

    async def _schedule_debounced(self, path_str: str, event_type: str) -> None:
        path = Path(path_str)
        if _should_ignore(path):
            return

        if path_str in self._debounce:
            _, existing_task = self._debounce[path_str]
            existing_task.cancel()

        task = asyncio.create_task(self._delayed_emit(path_str, event_type))
        self._debounce[path_str] = (event_type, task)

    async def _delayed_emit(self, path_str: str, event_type: str) -> None:
        try:
            await asyncio.sleep(_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return

        final_type, _ = self._debounce.pop(path_str, (event_type, None))
        path = Path(path_str)
        await self.emit(WadeEvent(
            event_type=EventType.FS_CHANGE,
            payload={"name": path.name, "path": path_str, "event_type": final_type},
            source="monitor:filesystem",
        ))
        logger.debug("[FILESYSTEM] Debounced emit: %s %s", final_type, path.name)

    def change_watch_dir(self, new_path: Path) -> None:
        self._watch_dir = new_path
        self._stop_observer()

        if self._loop is None:
            logger.info("[FILESYSTEM] Watch dir queued to %s (monitor not running yet)", new_path)
            return

        self._start_observer()
        logger.info("[FILESYSTEM] Watch dir changed to %s", new_path)

    def get_extra_status(self) -> dict:
        return {
            "watch_path":      str(self._watch_dir),
            "recursive":       self._recursive,
            "debounce_pending": len(self._debounce),
        }


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
