from __future__ import annotations

import json
import time
import logging
import asyncio
import aiohttp

from pathlib import Path
from typing import Dict, List, Optional

from app.core.hardware import probe_hardware

logger = logging.getLogger(__name__)

OLLAMADB_BASE = "https://ollamadb.dev/api/v1"
HTTP_TIMEOUT  = aiohttp.ClientTimeout(total=8)

CACHE_FILE = Path.home() / ".wade" / "model_catalog.json"
CACHE_TTL  = 7 * 24 * 3600

SIZE_LADDER: List[tuple[float, str]] = [
    (240.0, "405b"),
    (46.0,  "72b"),
    (44.0,  "70b"),
    (20.0,  "32b"),
    (9.5,   "14b"),
    (8.5,   "13b"),
    (7.5,   "11b"),
    (6.5,   "9b"),
    (5.5,   "8b"),
    (4.5,   "7b"),
    (2.5,   "3b"),
    (1.5,   "1.5b"),
    (1.0,   "1b"),
]

FIXED_TAG_MODELS: set[str] = {
    "nomic-embed-text",
    "mxbai-embed-large",
    "snowflake-arctic-embed",
    "all-minilm",
}

ROLE_QUERIES: Dict[str, dict] = {
    "chat":      {"search": "instruct",  "label_exclude": ["embed", "vision", "code"]},
    "coding":    {"search": "coder",     "label_require": "code", "label_exclude": ["embed", "vision"]},
    "reasoning": {"search": "r1",        "label_exclude": ["embed", "vision"]},
    "embedding": {"search": "embed",     "label_require": "embed"},
    "vision":    {"search": "vision",    "label_require": "vision"},
    "fast":      {"search": "instruct",  "label_exclude": ["embed", "vision", "code"], "max_vram_gb": 2.5},
}

FALLBACK_FAMILIES: Dict[str, List[str]] = {
    "chat":      ["qwen2.5",         "llama3.1",          "mistral",    "gemma2"],
    "coding":    ["qwen2.5-coder",   "deepseek-coder-v2", "codellama"],
    "reasoning": ["deepseek-r1",     "qwq"],
    "embedding": ["nomic-embed-text","mxbai-embed-large"],
    "vision":    ["llama3.2-vision", "llava",             "minicpm-v"],
    "fast":      ["llama3.2",        "qwen2.5",           "phi4-mini"],
}

_DISPLAY_SUITES: Dict[str, Dict[str, str]] = {
    "xl":     {"chat": "qwen2.5:72b",    "coding": "qwen2.5-coder:32b", "reasoning": "deepseek-r1:32b",  "embedding": "nomic-embed-text", "vision": "llama3.2-vision:11b", "fast": "qwen2.5:14b"},
    "large":  {"chat": "qwen2.5:32b",    "coding": "qwen2.5-coder:14b", "reasoning": "deepseek-r1:14b",  "embedding": "nomic-embed-text", "vision": "llama3.2-vision:11b", "fast": "qwen2.5:7b"},
    "medium": {"chat": "llama3.1:8b",    "coding": "qwen2.5-coder:7b",  "reasoning": "deepseek-r1:8b",   "embedding": "nomic-embed-text", "vision": "llava:7b",            "fast": "llama3.2:3b"},
    "small":  {"chat": "llama3.2:3b",    "coding": "qwen2.5-coder:3b",  "reasoning": "deepseek-r1:1.5b", "embedding": "nomic-embed-text", "vision": "llava:7b",            "fast": "llama3.2:1b"},
    "tiny":   {"chat": "llama3.2:1b",    "coding": "qwen2.5-coder:1.5b","reasoning": "deepseek-r1:1.5b", "embedding": "nomic-embed-text", "vision": "llava:7b",            "fast": "llama3.2:1b"},
}

def select_profile(usable_mem_gb: float) -> str:
    """Classify hardware into one of five tiers for display/logging."""
    if usable_mem_gb >= 38.0: return "xl"
    if usable_mem_gb >= 18.0: return "large"
    if usable_mem_gb >= 6.0:  return "medium"
    if usable_mem_gb >= 3.0:  return "small"
    return "tiny"

def get_suite(profile: str) -> Dict[str, str]:
    """Return the static display suite for a profile (wizard use only)."""
    return _DISPLAY_SUITES.get(profile, _DISPLAY_SUITES["tiny"])

def best_tag(family: str, usable_mem_gb: float, max_vram_gb: Optional[float] = None) -> str:
    """Return `family:tag` for the largest model size that fits in memory. For fixed-tag models (embedding, etc.) returns just the family name."""
    if family in FIXED_TAG_MODELS:
        return family
    ceiling = min(usable_mem_gb, max_vram_gb) if max_vram_gb else usable_mem_gb
    for vram_req, tag in SIZE_LADDER:
        if vram_req <= ceiling * 0.85:
            return f"{family}:{tag}"
    return family

def _load_cache() -> Optional[Dict[str, List[str]]]:
    try:
        if CACHE_FILE.exists():
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                return data.get("catalog")
    except Exception:
        pass
    return None

def _save_cache(catalog: Dict[str, List[str]]) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps({"timestamp": time.time(), "catalog": catalog}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

async def _fetch_top_family(
    session: aiohttp.ClientSession,
    role: str,
    cfg: dict,
) -> Optional[str]:
    """Query ollamadb.dev and return the model_identifier of the best match."""
    params = {
        "search":     cfg["search"],
        "model_type": "official",
        "sort_by":    "pulls",
        "order":      "desc",
        "limit":      20,
    }
    try:
        async with session.get(f"{OLLAMADB_BASE}/models", params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logger.debug("[discovery] fetch failed for role=%s: %s", role, e)
        return None

    required = cfg.get("label_require", "").lower()
    excluded = {e.lower() for e in cfg.get("label_exclude", [])}

    for model in data.get("models", []):
        labels = " ".join(l.lower() for l in (model.get("labels") or []))
        name   = model.get("model_identifier", "").lower()

        if required and required not in labels and required not in name:
            continue
        if excluded and any(ex in labels or ex in name for ex in excluded):
            continue

        family = model.get("model_identifier")
        if family:
            logger.debug("[discovery] role=%s → %s (%d pulls)", role, family, model.get("pulls", 0))
            return family

    return None

async def _discover_catalog() -> Optional[Dict[str, List[str]]]:
    """Fetch the top model family for each role from ollamadb.dev, applying label filters. Returns a dict like: {role: [top_family, ...fallbacks]} or None on failure."""
    catalog: Dict[str, List[str]] = {}
    try:
        connector = aiohttp.TCPConnector(ssl=True)
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, connector=connector) as session:
            results = await asyncio.gather(
                *[_fetch_top_family(session, role, cfg) for role, cfg in ROLE_QUERIES.items()],
                return_exceptions=True,
            )
        for (role, _), result in zip(ROLE_QUERIES.items(), results):
            if isinstance(result, str) and result:
                fallbacks = [f for f in FALLBACK_FAMILIES.get(role, []) if f != result]
                catalog[role] = [result] + fallbacks
    except Exception as e:
        logger.warning("[discovery] catalog fetch error: %s", e)
        return None

    return catalog if len(catalog) >= 3 else None

async def async_generate_optimal_suite() -> Dict[str, str]:
    """Determine the optimal model suite for the current hardware by querying ollamadb.dev with role-specific filters, applying local caching, and falling back to built-in recommendations if needed. Returns a dict mapping each role to a specific model tag (e.g. 'qwen2.5:14b')."""
    specs      = probe_hardware()
    primary    = specs.get("primary", {})
    usable_mem = primary.get("memory_usable_gb", 0.0)
    profile    = select_profile(usable_mem)

    logger.info(
        "[discovery] Hardware: profile=%s  usable=%.1f GB  backend=%s",
        profile, usable_mem, primary.get("backend", "cpu"),
    )

    catalog = _load_cache()

    if catalog is None:
        print("  Checking Ollama library for latest model rankings...", flush=True)
        catalog = await _discover_catalog()
        if catalog:
            _save_cache(catalog)
            print("  Model catalog refreshed.", flush=True)
        else:
            print("  Ollama library unreachable — using built-in recommendations.", flush=True)
            catalog = FALLBACK_FAMILIES

    suite: Dict[str, str] = {}
    for role, families in catalog.items():
        cfg      = ROLE_QUERIES.get(role, {})
        max_vram = cfg.get("max_vram_gb")
        family   = families[0] if isinstance(families, list) else families
        suite[role] = best_tag(family, usable_mem, max_vram)

    return suite