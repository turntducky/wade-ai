from __future__ import annotations

import json
import logging

from pathlib import Path

logger = logging.getLogger("wade.proactive_prefs")

_PREFS_PATH = Path.home() / ".wade" / "proactive_prefs.json"

_DEFAULT: dict = {
    "suppressed":        [],          # list of suppressed topic strings
    "engagement":        {},          # topic → float score (0.0–1.0)
    "feedback_log":      [],          # last 200 feedback entries
}

_MAX_FEEDBACK_LOG = 200


def load() -> dict:
    try:
        if _PREFS_PATH.exists():
            data = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
            for k, v in _DEFAULT.items():
                data.setdefault(k, v)
            return data
    except Exception as e:
        logger.warning("[PREFS] Failed to load proactive prefs: %s", e)
    return dict(_DEFAULT)


def save(prefs: dict) -> None:
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[PREFS] Failed to save proactive prefs: %s", e)


def suppress(topic: str) -> None:
    prefs = load()
    if topic not in prefs["suppressed"]:
        prefs["suppressed"].append(topic)
        save(prefs)
        logger.info("[PREFS] Suppressed topic: %s", topic)


def unsuppress(topic: str) -> None:
    prefs = load()
    if topic in prefs["suppressed"]:
        prefs["suppressed"].remove(topic)
        save(prefs)
        logger.info("[PREFS] Un-suppressed topic: %s", topic)


def record_feedback(message_id: str, topic: str, signal: str) -> None:
    """
    signal: "engaged" (user replied within ~3 min) or "ignored"
    Updates an EMA engagement score for the topic.
    """
    prefs = load()

    score = prefs["engagement"].get(topic, 0.5)
    weight = 1.0 if signal == "engaged" else 0.0
    prefs["engagement"][topic] = round(0.7 * score + 0.3 * weight, 4)

    entry = {"id": message_id, "topic": topic, "signal": signal}
    prefs["feedback_log"].append(entry)
    if len(prefs["feedback_log"]) > _MAX_FEEDBACK_LOG:
        prefs["feedback_log"] = prefs["feedback_log"][-_MAX_FEEDBACK_LOG:]

    save(prefs)
    logger.debug("[PREFS] Feedback recorded: %s → %s (score now %.2f)", topic, signal, prefs["engagement"][topic])


def get_engagement_score(topic: str) -> float:
    prefs = load()
    return prefs["engagement"].get(topic, 0.5)


def get_suppressed() -> list[str]:
    return load().get("suppressed", [])
