from __future__ import annotations

import psutil
import random
import asyncio
import logging

from pathlib import Path
from typing import Callable
from collections import deque
from datetime import datetime

from app.core.config import ConfigManager
from app.memory.manager import append_to_memory

logger = logging.getLogger("wade.proactive")

COOLDOWN_MINUTES   = 15
IDLE_CHECK_MINUTES = 20
MAX_PER_HOUR       = 4

_PROMPTS: dict[str, list[str]] = {
    "morning": [
        "It's morning. Greet the user with a brief, Jarvis-style good morning. "
        "Reference the time. Be alert and forward-looking. One or two sentences only.",

        "Offer a brief morning status check — note you're ready and ask if there's "
        "anything that needs attention today. One sentence, Jarvis style.",
    ],
    "afternoon": [
        "It's the afternoon. Make a short, dry observation — you've been running, "
        "everything is in order, you're available. One sentence.",

        "Send a brief mid-afternoon check-in. Understated. Confident. Not chatty.",
    ],
    "evening": [
        "It's evening. Send a short, slightly lighter check-in — the day is winding "
        "down, you're still here. One or two sentences, Jarvis style.",

        "Acknowledge it's evening with quiet wit. Offer to help wrap up anything "
        "before the end of the day. One sentence.",
    ],
    "late_night": [
        "It's late. Send a single-sentence message acknowledging the hour — "
        "understated, not dramatic. Jarvis style.",
    ],
    "idle": [
        "The user hasn't said anything in a while. Send a single casual, dry "
        "check-in — not needy, just present. One sentence. Jarvis style.",

        "Make a brief, witty observation about the silence. Not clingy. One sentence.",
    ],
    "system_ok": [
        "Everything looks fine on the system side. Make a single brief remark "
        "noting all is well — the kind of thing Jarvis would say in passing.",
    ],
}

def _time_bucket() -> str:
    hour = datetime.now().hour
    if 6  <= hour < 12: return "morning"
    if 12 <= hour < 17: return "afternoon"
    if 17 <= hour < 22: return "evening"
    return "late_night"

import re as _re

def _strip_message_wrapper(text: str) -> str:
    text = _re.sub(
        r"^(sure[!,.]?\s*|certainly[!,.]?\s*)?"
        r"(here(?:'s| is) the message[:\s]*|here(?:'s| is) what (?:i'?d? ?say|you could say)[:\s]*)",
        "",
        text,
        flags=_re.IGNORECASE,
    ).strip()
    text = _re.sub(r"^```[a-z]*\n?(.*?)\n?```$", r"\1", text, flags=_re.DOTALL | _re.IGNORECASE).strip()
    text = _re.sub(r'^[""](.*)[""]$', r"\1", text, flags=_re.DOTALL).strip()
    return text

_MESSAGE_BUFFER_SIZE = 50

class ProactiveEngine:
    def __init__(self) -> None:
        self._clients:             list[asyncio.Queue] = []
        self._lock                 = asyncio.Lock()
        self._last_sent:           datetime | None = None
        self._sent_count           = 0
        self._hour_bucket          = datetime.now().hour
        self._user_last_active:    datetime | None = None
        self._inference_fn:        Callable | None = None
        self._boot_done            = False
        self._pending_fs_events:   dict[str, str]  = {}
        self._message_buffer:      deque[dict]     = deque(maxlen=_MESSAGE_BUFFER_SIZE)

    def set_inference_fn(self, fn: Callable) -> None:
        self._inference_fn = fn

    def bind_task_store(self, store) -> None:
        """Bind the task store so the engine can read active tasks for context."""
        self._task_store = store

    def record_fs_event(self, name: str, event_type: str) -> None:
        """Called by ProactiveMonitor when a FS_CHANGE event arrives on the bus."""
        self._pending_fs_events[name] = event_type

    def _pop_fs_events(self) -> list[str]:
        if not self._pending_fs_events:
            return []
        events = [f"'{name}' was {et}" for name, et in self._pending_fs_events.items()]
        self._pending_fs_events.clear()
        return events

    async def _get_current_context(self) -> dict:
        if not hasattr(self, "_task_store") or not self._task_store:
            logger.warning("[PROACTIVE] Task store not bound. Running blind.")
            return {"cpu": 0.0, "ram": 0.0, "tasks": [], "hour": datetime.now().hour, "files": []}

        cpu           = psutil.cpu_percent(interval=None)
        ram           = psutil.virtual_memory().percent
        active_tasks  = self._task_store.list_active()
        pending_goals = [t.goal for t in active_tasks]
        hour          = datetime.now().hour
        recent_files  = self._pop_fs_events()

        return {"cpu": cpu, "ram": ram, "tasks": pending_goals, "hour": hour, "files": recent_files}

    def notify_user_active(self) -> None:
        self._user_last_active = datetime.now()

    async def register(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._clients.append(q)
        logger.debug("SSE client registered (%d total)", len(self._clients))

    async def unregister(self, q: asyncio.Queue) -> None:
        async with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass
        logger.debug("SSE client unregistered (%d remaining)", len(self._clients))

    async def run(self) -> None:
        logger.info("[PROACTIVE] Engine started. Saliency loop active.")
        await asyncio.sleep(10)
        await self._handle_boot()
        while True:
            await asyncio.sleep(60)
            await self._evaluate_and_act()

    def _can_send_routine(self) -> bool:
        now = datetime.now()
        if now.hour != self._hour_bucket:
            self._hour_bucket = now.hour
            self._sent_count  = 0
        if self._sent_count >= MAX_PER_HOUR:
            return False
        if self._last_sent:
            elapsed = (now - self._last_sent).total_seconds() / 60
            if elapsed < COOLDOWN_MINUTES:
                return False
        if self._user_last_active:
            since_active = (now - self._user_last_active).total_seconds() / 60
            if since_active < 3:
                return False
        return True

    async def _evaluate_and_act(self) -> None:
        if not self._clients:
            return

        state  = await self._get_current_context()
        config = ConfigManager.get().get("monitors", {}).get("system", {})
        cpu_t  = float(config.get("cpu_threshold", 85.0))
        ram_t  = float(config.get("ram_threshold",  90.0))

        if state["cpu"] > cpu_t or state["ram"] > ram_t:
            urgent_prompt = (
                f"System State: CPU {state['cpu']:.1f}%, RAM {state['ram']:.1f}%. "
                f"Thresholds: CPU {cpu_t}%, RAM {ram_t}%. "
                "Priority: URGENT. "
                "Directive: The system is under heavy load. Be direct, professional, "
                "and slightly concerned. Suggest they check the processes. One sentence. Jarvis-style."
            )
            text = await self._generate(urgent_prompt)
            if text:
                await self._broadcast(text)
            return

        if self._can_send_routine():
            is_idle = (
                self._user_last_active is not None
                and (datetime.now() - self._user_last_active).total_seconds() / 60 > IDLE_CHECK_MINUTES
            )
            if not is_idle and random.random() > 0.40:
                return

            passive_prompt = (
                f"System State: CPU {state['cpu']}%, RAM {state['ram']}%. "
                f"Active Tasks: {state['tasks'] if state['tasks'] else 'None'}. "
                f"Recent File Changes: {state['files'] if state['files'] else 'None'}. "
                f"Time: {state['hour']}:00. User Idle: {is_idle}. "
                "Priority: ROUTINE. "
                "Directive: You are W.A.D.E. Decide if you should speak. "
                "If everything is normal and tasks are empty, output 'SILENCE'. "
                "If a task is pending, or it's a new time of day, or the user is idle, "
                "generate a brief, witty, Jarvis-style observation. "
                "CRITICAL: If you have nothing useful to say, your exact output must be 'SILENCE'."
            )
            text = await self._generate(passive_prompt)
            if text and "SILENCE" not in text.upper():
                await self._broadcast(text)

    def get_pending_messages(self, n: int = 20) -> list[dict]:
        """Return the last *n* broadcast messages (newest last) for state sync."""
        items = list(self._message_buffer)
        return items[-n:] if n < len(items) else items

    async def _broadcast(self, text: str) -> None:
        if not self._clients:
            return
        async with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                await q.put({"type": "proactive_message", "content": text})
            except Exception:
                pass
        self._message_buffer.append({
            "content": text,
            "timestamp": datetime.now().isoformat(),
        })
        try:
            append_to_memory(role="W.A.D.E.", text=text)
        except Exception as e:
            logger.warning("[PROACTIVE] Failed to persist message to memory: %s", e)
        self._last_sent   = datetime.now()
        self._sent_count += 1
        logger.info("[PROACTIVE] Broadcast: %s", text[:80] + ("…" if len(text) > 80 else ""))

    async def _generate(self, prompt: str) -> str | None:
        if not self._inference_fn:
            return None
        directive = (
            f"{prompt}\n\n"
            "CRITICAL: Your response IS the message — output it directly with no preamble. "
            "Do NOT say 'Here is the message', 'Sure!', 'Certainly', or anything similar. "
            "Do NOT wrap the text in code fences or quotes. Speak as W.A.D.E. to the user."
        )
        try:
            full = ""
            async for chunk in self._inference_fn(directive, is_system=True):
                if not chunk.startswith(("\n\n⚙️", "[⚠️", "[System")):
                    full += chunk
            text = _strip_message_wrapper(full.strip())
            return text if text and text != "HEARTBEAT_OK" else None
        except Exception as e:
            logger.warning("[PROACTIVE] Inference error: %s", e)
            return None

    async def _handle_boot(self) -> None:
        if self._boot_done:
            return
        self._boot_done = True

        if hasattr(self, "_task_store") and self._task_store:
            if self._task_store.list_active():
                logger.info("[PROACTIVE] User already active on boot. Aborting boot sequence.")
                return

        bucket = _time_bucket()
        hour   = datetime.now().hour
        boot_prompt = (
            f"It is {bucket} ({hour}:00). You have just come online. "
            "Send a single Jarvis-style boot announcement — note the time of day "
            "naturally, confirm you are ready. One sentence only. "
            "Do not use the word 'boot'. Do not use filler like 'Certainly'."
        )
        intro = await self._generate(boot_prompt)
        if intro:
            await self._broadcast(intro)
            self._last_sent   = datetime.now()
            self._sent_count += 1

        bootstrap_file = Path.home() / ".wade" / "workspace" / "BOOTSTRAP.md"
        if bootstrap_file.exists():
            await asyncio.sleep(2)
            onboard_prompt = (
                "This is your very first session with this user. "
                "Send a brief, warm but professional Jarvis-style message: "
                "let them know you'd like to ask a few quick questions to get "
                "properly acquainted before you get to work. "
                "One or two sentences. Conversational, not robotic."
            )
            onboard_msg = await self._generate(onboard_prompt)
            if onboard_msg:
                await self._broadcast(onboard_msg)
                self._last_sent   = datetime.now()
                self._sent_count += 1

proactive_engine = ProactiveEngine()