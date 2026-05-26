from __future__ import annotations

import re as _re
import uuid
import psutil
import random
import asyncio
import logging

from pathlib import Path
from typing import Callable, TYPE_CHECKING
from collections import deque
from datetime import datetime

from app.core.config import ConfigManager
from app.memory.manager import append_to_memory
from app.services import proactive_prefs as _prefs

if TYPE_CHECKING:
    from app.core.events import InternalEventBus

logger = logging.getLogger("wade.proactive")

COOLDOWN_MINUTES   = 15
IDLE_CHECK_MINUTES = 20
MAX_PER_HOUR       = 4

# ── Intent detection ──────────────────────────────────────────────────────────

_CODE_KEYWORDS     = {"python", "code", "script", "debug", "error", "function", "class",
                      "import", "test", "build", "compile", "deploy", "refactor", "bug",
                      "commit", "merge", "branch", "api", "endpoint"}
_RESEARCH_KEYWORDS = {"research", "search", "find", "look up", "investigate", "analyze",
                      "report", "summarize", "read", "article", "topic", "explain"}
_WRITING_KEYWORDS  = {"write", "draft", "document", "readme", "email", "message",
                      "blog", "post", "compose", "edit", "proofread"}
_CODE_EXTENSIONS   = {".py", ".js", ".ts", ".go", ".rs", ".java", ".cpp", ".c",
                      ".cs", ".rb", ".php", ".sh", ".yaml", ".toml"}

def _detect_intent(goals: list[str], files: list[str]) -> str:
    text = " ".join(goals + files).lower()
    code_score     = sum(1 for k in _CODE_KEYWORDS     if k in text)
    research_score = sum(1 for k in _RESEARCH_KEYWORDS if k in text)
    writing_score  = sum(1 for k in _WRITING_KEYWORDS  if k in text)
    code_score    += sum(1 for f in files if any(f.lower().endswith(e) for e in _CODE_EXTENSIONS))

    if code_score > research_score and code_score > writing_score and code_score > 0:
        return "coding"
    if research_score > writing_score and research_score > 0:
        return "research"
    if writing_score > 0:
        return "writing"
    return "idle"


# ── Prompt templates ─────────────────────────────────────────────────────────

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
    "coding": [
        "The user appears to be working on code. Make a brief, technically relevant "
        "observation or offer — something a senior engineer might say in passing. "
        "Do not summarize what they're doing. One sentence.",

        "The user is coding. Acknowledge you're available for technical questions "
        "or code review. One sentence, Jarvis-style. Don't explain what coding is.",
    ],
    "research": [
        "The user appears to be doing research. Offer quietly to help surface "
        "information or synthesize findings. One sentence, Jarvis-style.",
    ],
    "writing": [
        "The user is writing. Offer briefly to help review or refine when they're "
        "ready. One sentence, understated.",
    ],
    "arrival": [
        "The user just returned after some time away. Welcome them back briefly — "
        "note you've been running and are ready to pick up where they left off. "
        "One sentence, Jarvis-style.",
    ],
}


def _time_bucket() -> str:
    hour = datetime.now().hour
    if 6  <= hour < 12: return "morning"
    if 12 <= hour < 17: return "afternoon"
    if 17 <= hour < 22: return "evening"
    return "late_night"


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
        self._clients:           list[asyncio.Queue] = []
        self._lock               = asyncio.Lock()
        self._last_sent:         datetime | None = None
        self._sent_count         = 0
        self._hour_bucket        = datetime.now().hour
        self._user_last_active:  datetime | None = None
        self._inference_fn:      Callable | None = None
        self._bus:               "InternalEventBus | None" = None
        self._boot_done          = False
        self._pending_fs_events: dict[str, str] = {}
        self._message_buffer:    deque[dict]    = deque(maxlen=_MESSAGE_BUFFER_SIZE)
        # Feedback tracking: last broadcast id → topic
        self._last_message_id:   str | None = None
        self._last_message_topic: str | None = None

    # ── Wiring ────────────────────────────────────────────────────────────────

    def set_inference_fn(self, fn: Callable) -> None:
        self._inference_fn = fn

    def bind_task_store(self, store) -> None:
        self._task_store = store

    def bind_bus(self, bus: "InternalEventBus") -> None:
        self._bus = bus

    # ── Filesystem event buffer ───────────────────────────────────────────────

    def record_fs_event(self, name: str, event_type: str) -> None:
        self._pending_fs_events[name] = event_type

    def _pop_fs_events(self) -> list[str]:
        if not self._pending_fs_events:
            return []
        events = [f"'{name}' was {et}" for name, et in self._pending_fs_events.items()]
        self._pending_fs_events.clear()
        return events

    # ── User activity tracking ────────────────────────────────────────────────

    def notify_user_active(self) -> None:
        prev = self._user_last_active
        self._user_last_active = datetime.now()

        # Detect arrival: user returning after being idle for more than IDLE_CHECK_MINUTES
        if prev is not None and self._bus is not None:
            idle_minutes = (datetime.now() - prev).total_seconds() / 60
            if idle_minutes > IDLE_CHECK_MINUTES:
                from app.core.events import WadeEvent, EventType
                self._bus.emit_nowait(WadeEvent(
                    event_type=EventType.USER_ARRIVAL,
                    payload={"idle_minutes": round(idle_minutes, 1)},
                    source="proactive_engine",
                ))

        # Detect engagement: user replied within 3 minutes of last proactive message
        if self._last_message_id and self._last_message_topic and self._last_sent:
            elapsed = (datetime.now() - self._last_sent).total_seconds() / 60
            if elapsed <= 3.0:
                _prefs.record_feedback(self._last_message_id, self._last_message_topic, "engaged")
                self._last_message_id    = None
                self._last_message_topic = None

    async def on_user_arrival(self, idle_minutes: float) -> None:
        """Called by ProactiveMonitor when USER_ARRIVAL fires. Sends a welcome-back message."""
        if not self._clients:
            return
        topic = "arrival"
        if _prefs.get_engagement_score(topic) < 0.2 or topic in _prefs.get_suppressed():
            return
        prompt_text = random.choice(_PROMPTS["arrival"])
        prompt = (
            f"The user just returned after {idle_minutes:.0f} minutes away. "
            f"{prompt_text}"
        )
        text = await self._generate(prompt)
        if text:
            await self._broadcast(text, topic=topic)

    # ── Context assembly ──────────────────────────────────────────────────────

    async def _get_current_context(self) -> dict:
        if not hasattr(self, "_task_store") or not self._task_store:
            logger.warning("[PROACTIVE] Task store not bound.")
            return {
                "cpu": 0.0, "ram": 0.0, "tasks": [], "files": [],
                "hour": datetime.now().hour, "intent": "idle", "event_summary": {},
            }

        cpu          = psutil.cpu_percent(interval=None)
        ram          = psutil.virtual_memory().percent
        active_tasks = self._task_store.list_active()
        goals        = [t.goal for t in active_tasks]
        hour         = datetime.now().hour
        recent_files = self._pop_fs_events()
        intent       = _detect_intent(goals, recent_files)
        event_summary = self._bus.get_rolling_summary() if self._bus else {}

        return {
            "cpu": cpu, "ram": ram,
            "tasks": goals, "files": recent_files,
            "hour": hour, "intent": intent,
            "event_summary": event_summary,
        }

    # ── SSE client management ─────────────────────────────────────────────────

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

    def get_pending_messages(self, n: int = 20) -> list[dict]:
        items = list(self._message_buffer)
        return items[-n:] if n < len(items) else items

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        logger.info("[PROACTIVE] Engine started.")
        await asyncio.sleep(10)
        await self._handle_boot()
        while True:
            await asyncio.sleep(60)
            await self._evaluate_and_act()

    # ── Rate-limit gate ───────────────────────────────────────────────────────

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

    # ── Decision engine ───────────────────────────────────────────────────────

    async def _evaluate_and_act(self) -> None:
        if not self._clients:
            return

        state  = await self._get_current_context()
        config = ConfigManager.get().get("monitors", {}).get("system", {})
        cpu_t  = float(config.get("cpu_threshold", 85.0))
        ram_t  = float(config.get("ram_threshold",  90.0))

        # Urgent: system overload (handled via task injection in ProactiveMonitor,
        # but also inline here if the proactive loop catches a live spike)
        if state["cpu"] > cpu_t or state["ram"] > ram_t:
            topic = "system_alert"
            if topic not in _prefs.get_suppressed():
                urgent_prompt = (
                    f"System load: CPU {state['cpu']:.1f}%, RAM {state['ram']:.1f}%. "
                    f"Thresholds: CPU {cpu_t}%, RAM {ram_t}%. Priority: URGENT. "
                    "Be direct and professional. Suggest checking the processes. One sentence."
                )
                text = await self._generate(urgent_prompt)
                if text:
                    await self._broadcast(text, topic=topic)
            return

        if not self._can_send_routine():
            return

        intent  = state["intent"]
        is_idle = (
            self._user_last_active is not None
            and (datetime.now() - self._user_last_active).total_seconds() / 60 > IDLE_CHECK_MINUTES
        )

        # Pick topic and prompt
        if is_idle:
            topic = "idle"
        elif intent in _PROMPTS and intent != "idle":
            topic = intent
        else:
            topic = _time_bucket()

        # Check suppression
        if topic in _prefs.get_suppressed():
            return

        # Bias send probability by engagement score
        score = _prefs.get_engagement_score(topic)
        base_threshold = 0.40
        # Low engagement → raise bar (send less); high engagement → lower bar (send more)
        adjusted_threshold = base_threshold + (0.5 - score) * 0.4
        if random.random() > (1.0 - adjusted_threshold):
            return

        prompt_template = random.choice(_PROMPTS.get(topic, _PROMPTS["system_ok"]))
        event_counts = state.get("event_summary", {}).get("counts", {})
        event_line   = (
            ", ".join(f"{k}: {v}" for k, v in event_counts.items())
            if event_counts else "None"
        )
        passive_prompt = (
            f"System State: CPU {state['cpu']}%, RAM {state['ram']}%. "
            f"Active Tasks: {state['tasks'] if state['tasks'] else 'None'}. "
            f"Recent File Changes: {state['files'] if state['files'] else 'None'}. "
            f"Event Bus Activity (last 100 events): {event_line}. "
            f"Detected User Intent: {intent}. Time: {state['hour']}:00. "
            f"User Idle: {is_idle}. "
            "Priority: ROUTINE. "
            f"Directive: {prompt_template} "
            "If you have nothing useful to say given the context, your exact output must be 'SILENCE'."
        )
        text = await self._generate(passive_prompt)
        if text and "SILENCE" not in text.upper():
            await self._broadcast(text, topic=topic)

    # ── Broadcast + feedback tracking ─────────────────────────────────────────

    async def _broadcast(self, text: str, topic: str = "routine") -> None:
        if not self._clients:
            return

        message_id = str(uuid.uuid4())
        self._last_message_id    = message_id
        self._last_message_topic = topic

        async with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                await q.put({"type": "proactive_message", "id": message_id, "content": text})
            except Exception:
                pass

        self._message_buffer.append({
            "id":        message_id,
            "content":   text,
            "topic":     topic,
            "timestamp": datetime.now().isoformat(),
        })
        try:
            append_to_memory(role="W.A.D.E.", text=text)
        except Exception as e:
            logger.warning("[PROACTIVE] Failed to persist message: %s", e)

        self._last_sent   = datetime.now()
        self._sent_count += 1
        logger.info("[PROACTIVE] Broadcast [%s]: %s", topic, text[:80] + ("…" if len(text) > 80 else ""))

    def record_feedback(self, message_id: str, signal: str) -> None:
        """External API hook for explicit user feedback (seen / dismissed)."""
        for entry in reversed(self._message_buffer):
            if entry.get("id") == message_id:
                _prefs.record_feedback(message_id, entry.get("topic", "routine"), signal)
                return

    # ── Inference ─────────────────────────────────────────────────────────────

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

    # ── Boot sequence ─────────────────────────────────────────────────────────

    async def _handle_boot(self) -> None:
        if self._boot_done:
            return
        self._boot_done = True

        if hasattr(self, "_task_store") and self._task_store:
            if self._task_store.list_active():
                logger.info("[PROACTIVE] User already active on boot. Skipping boot message.")
                return

        bucket = _time_bucket()
        hour   = datetime.now().hour
        boot_prompt = (
            f"It is {bucket} ({hour}:00). You have just come online. "
            "Send a single Jarvis-style boot announcement — note the time naturally, "
            "confirm you are ready. One sentence only. "
            "Do not use the word 'boot'. No filler like 'Certainly'."
        )
        intro = await self._generate(boot_prompt)
        if intro:
            await self._broadcast(intro, topic=_time_bucket())
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
                await self._broadcast(onboard_msg, topic="onboarding")
                self._last_sent   = datetime.now()
                self._sent_count += 1


proactive_engine = ProactiveEngine()
