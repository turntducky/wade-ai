import re
import json
import asyncio
import logging
import threading

from pathlib import Path
from datetime import datetime, timedelta

from app.memory.md_patcher import patch_if_mapped

_user_facts_lock = threading.Lock()

logger = logging.getLogger("wade_passive_extractor")

_SELF_REF = re.compile(
    r"\b(my|i(?:'m| am| was| have| work| live| go by|'ve| prefer| love| like| hate| own| got|'ll| will|'d| would))\b",
    re.IGNORECASE,
)
_WADE_REF = re.compile(
    r"\b(your|you(?:'re| are|'ve)|call yourself|you were)\b",
    re.IGNORECASE,
)
_PURE_QUESTION = re.compile(
    r"^\s*(?:what|who|when|where|why|how|is|are|can|could|would|should|do|does|did|will|have|has)\b",
    re.IGNORECASE,
)
_MIN_WORDS = 6

def _should_extract(text: str) -> bool:
    """Stage 1: cheap triage — returns True only if message may contain durable facts."""
    stripped = text.strip()
    words = stripped.split()
    if len(words) < _MIN_WORDS:
        return False
    if _PURE_QUESTION.match(stripped):
        return False
    return bool(_SELF_REF.search(stripped) or _WADE_REF.search(stripped))

_MONTH_NAMES = (
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
)

_DAY_ORDINAL = r"\d{1,2}(?:st|nd|rd|th)?"
_DATE_FRAGMENT = (
    rf"(?:{_MONTH_NAMES}\s+{_DAY_ORDINAL}"
    rf"|{_DAY_ORDINAL}\s+(?:of\s+)?{_MONTH_NAMES}"
    rf"|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?)"
)

_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    # -- Birthday --------------------------------------------------------------
    (
        "User: Birthday",
        re.compile(
            rf"\b(?:my birthday is|my birthday was|i was born on|born on|my bday is|my bday was)\s+({_DATE_FRAGMENT})",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- Preferred name --------------------------------------------------------
    (
        "User: Name",
        re.compile(
            r"\bmy name is\s+([A-Za-z][A-Za-z'\-]{0,29})(?:\s*[,.]|\s+and\b|$)",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- Nickname / handle ------------------------------------------------------
    (
        "User: Nickname",
        re.compile(
            r"\b(?:i go by|call me|my nickname is|my handle is|my username is|everyone calls me)\s+([A-Za-z0-9][A-Za-z0-9_\-\.]{0,29})",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- Age ------------------------------------------------------------
    (
        "User: Age",
        re.compile(r"\bi(?:'m| am)\s+(\d{1,3})\s+years?\s+old\b", re.IGNORECASE),
        1,
    ),
    # -- Occupation — "I work as [a] ..." --------------------------
    (
        "User: Occupation",
        re.compile(
            r"\bi work as\s+(?:a(?:n)?\s+)?([A-Za-z][A-Za-z\s\-]{2,39}?)(?:\s*[,.]|\s+and\b|$)",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- Occupation — "I'm a [role] by [trade/profession]" ---------------------
    (
        "User: Occupation",
        re.compile(
            r"\bi(?:'m| am) a(?:n)?\s+([A-Za-z][A-Za-z\s\-]{2,39}?)\s+by\s+(?:trade|profession|training|career)",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- Occupation — "my job/profession/career is ..." -------------------------
    (
        "User: Occupation",
        re.compile(
            r"\bmy\s+(?:job|profession|career|occupation)\s+is\s+(?:a(?:n)?\s+)?([A-Za-z][A-Za-z\s\-]{2,39}?)(?:\s*[,.]|\s+and\b|$)",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- Location -------------------------------------------------------------
    (
        "User: Location",
        re.compile(
            r"\b(?:i live in|i(?:'m| am)\s+(?:from|based in|located in)|i(?:'m| am)\s+currently\s+in)\s+([A-Za-z][A-Za-z\s,\-\.]{2,49}?)(?:\s*[,.]|$)",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- Timezone -------------------------------------------------------------
    (
        "User: Timezone",
        re.compile(
            r"\bi(?:'m| am)\s+(?:in|on)\s+([A-Z]{2,5}(?:[+-]\d{1,2})?|UTC[+-]\d{1,2}|Eastern|Central|Mountain|Pacific|GMT[+-]\d{1,2}?)\b",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- Preferences / Likes -------------------------------------------------------------
    (
        "User: Likes",
        re.compile(
            r"\b(?:i prefer|i love|i really like|my favorite(?: \w+)? is)\s+([A-Za-z0-9\s\-\.]{2,40}?)(?:\s*[,.]|\s+because\b|$)",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- Dislikes -------------------------------------------------------------
    (
        "User: Dislikes",
        re.compile(
            r"\b(?:i hate|i dislike|i don't like|i can't stand)\s+([A-Za-z0-9\s\-\.]{2,40}?)(?:\s*[,.]|\s+because\b|$)",
            re.IGNORECASE,
        ),
        1,
    ),
    # -- W.A.D.E. nickname / identity ------------------------------------------------
    (
        "Wade: Nickname",
        re.compile(
            r"\b(?:your name is|call yourself|you(?:'re| are)\s+called|you go by)\s+([A-Za-z0-9][A-Za-z0-9_\-\.]{0,29})",
            re.IGNORECASE,
        ),
        1,
    ),
]

_YESTERDAY_BIRTHDAY = re.compile(r"\byesterday was my birthday\b", re.IGNORECASE)
_TODAY_BIRTHDAY = re.compile(r"\btoday is my birthday\b", re.IGNORECASE)

def _extract_facts(text: str, today: datetime | None = None) -> list[tuple[str, str]]:
    """Stage 2: extract facts via regexes and simple logic. Returns list of (topic_key, value) pairs."""
    if today is None:
        today = datetime.now()

    results: list[tuple[str, str]] = []
    seen_topics: set[str] = set()

    def _fmt_day(dt: datetime) -> str:
        for fmt in ("%B %#d", "%B %-d", "%B %d"):
            try:
                return dt.strftime(fmt).replace(" 0", " ")
            except ValueError:
                continue
        return dt.strftime("%B %d")

    if _YESTERDAY_BIRTHDAY.search(text):
        yesterday = today - timedelta(days=1)
        value = _fmt_day(yesterday)
        results.append(("User: Birthday", value))
        seen_topics.add("User: Birthday")

    if _TODAY_BIRTHDAY.search(text):
        value = _fmt_day(today)
        if "User: Birthday" not in seen_topics:
            results.append(("User: Birthday", value))
            seen_topics.add("User: Birthday")

    for topic_key, pattern, group_idx in _PATTERNS:
        if topic_key in seen_topics:
            continue
        m = pattern.search(text)
        if not m:
            continue
        try:
            value = m.group(group_idx).strip().rstrip(".,;")
        except IndexError:
            continue
        if not value or len(value) < 2:
            continue
        results.append((topic_key, value))
        seen_topics.add(topic_key)

    return results

_MEMORY_JSON = Path.home() / ".wade" / "workspace" / "memory.json"
_MEMORY_MD   = Path.home() / ".wade" / "workspace" / "MEMORY.md"

def _direct_store_fact(topic_key: str, value: str) -> bool:
    """Stage 3: directly store the extracted fact in memory.json. Returns True if stored, False if skipped (e.g. duplicate value)."""
    from app.skills.memory.updater import memory_file_lock, _load_memory_db, _save_memory_db

    with memory_file_lock:
        data = _load_memory_db()
        title_key = topic_key.strip().title()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if title_key in ["User: Likes", "User: Dislikes"]:
            existing = data.get(title_key, {}).get("fact", [])
            if isinstance(existing, str): 
                existing = [existing]
            
            if value.lower() in [item.lower() for item in existing]:
                return False
                
            existing.append(value)
            data[title_key] = {"fact": existing, "timestamp": timestamp}
            _save_memory_db(data)
            return True

        if title_key in data and data[title_key]["fact"] == value:
            return False

        data[title_key] = {"fact": value, "timestamp": timestamp}
        _save_memory_db(data)
        return True

def _store_user_fact(topic_key: str, value: str, facts_json: Path) -> bool:
    """Store an extracted fact to a per-user facts.json. Returns True if stored/updated."""
    with _user_facts_lock:
        data: dict = {}
        if facts_json.exists():
            try:
                data = json.loads(facts_json.read_text(encoding="utf-8"))
            except Exception:
                data = {}

        title_key = topic_key.strip().title()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if title_key in ["User: Likes", "User: Dislikes"]:
            existing = data.get(title_key, {}).get("fact", [])
            if isinstance(existing, str):
                existing = [existing]
            if value.lower() in [item.lower() for item in existing]:
                return False
            existing.append(value)
            data[title_key] = {"fact": existing, "timestamp": timestamp}
        else:
            if title_key in data and data[title_key].get("fact") == value:
                return False
            data[title_key] = {"fact": value, "timestamp": timestamp}

        facts_json.parent.mkdir(parents=True, exist_ok=True)
        facts_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True

async def extract_and_store(user_text: str, today: datetime | None = None, facts_file: Path | None = None) -> None:
    """Extract durable facts from user_text and persist them. When facts_file is provided (non-admin tiers), writes to that per-user facts.json instead of the admin memory.json."""
    try:
        if not _should_extract(user_text):
            return

        facts = await asyncio.to_thread(_extract_facts, user_text, today)
        if not facts:
            return

        for topic_key, value in facts:
            try:
                if facts_file is not None:
                    stored = await asyncio.to_thread(_store_user_fact, topic_key, value, facts_file)
                else:
                    stored = await asyncio.to_thread(_direct_store_fact, topic_key, value)
                if stored:
                    logger.debug("[PassiveMemory] Stored '%s' = '%s'", topic_key, value)
                    if facts_file is None:
                        await asyncio.to_thread(patch_if_mapped, topic_key, value)
            except Exception as e:
                logger.warning("[PassiveMemory] Failed to store fact '%s': %s", topic_key, e)

    except Exception as e:
        logger.warning("[PassiveMemory] Extraction error: %s", e)