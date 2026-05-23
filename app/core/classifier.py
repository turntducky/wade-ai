from __future__ import annotations

import re

from typing import Literal

_DESTRUCTIVE: frozenset[str] = frozenset([
    # System / File
    "rm", "delete", "wipe", "overwrite", "format", "reset",
    # Dev / DB
    "drop", "truncate", "kill", "terminate",
    # Privilege / system-level
    "sudo", "chmod", "chown", "env",
])

_FORCE_PUSH_RE = re.compile(r"push\s+--?force\b|push\s+-f\b|force\s+push", re.IGNORECASE)

_PATH_RE = re.compile(
    r"[/\\]|://|"
    r"\.(?:py|js|ts|sh|sql|db|pdf|txt|csv|json|yaml|yml|md|exe|bat|cmd)\b",
    re.IGNORECASE,
)

_CHAIN_RE = re.compile(
    r"\b(and|then|after|finally|next|also|additionally)\b",
    re.IGNORECASE,
)

def classify(goal: str) -> Literal["medium", "complex"]:
    """Classify the complexity of a goal based on heuristics."""
    words = frozenset(re.findall(r"\b\w+\b", goal.lower()))
    if (words & _DESTRUCTIVE) or _FORCE_PUSH_RE.search(goal):
        return "complex"
    if len(_CHAIN_RE.findall(goal)) >= 2:
        return "complex"
    return "medium"