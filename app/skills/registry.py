import sys
import time
import yaml
import logging
import threading
import importlib
import importlib.util

from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Callable, Awaitable, Tuple, Optional

logger = logging.getLogger("wade_skill_registry")

@dataclass
class SkillManifest:
    """Metadata block attached to every registered skill."""
    requires_network: bool                = False
    category:         str                 = "general"
    tier:             str                 = "common"
    reversible:       bool                = True
    instructions:     str                 = ""
    cacheable:        bool                = False
    cache_ttl:        int                 = 60
    risk:             str                 = "low"
    allowed_tiers:    Optional[list]      = None

_TOOL_CACHE: Dict[str, tuple] = {}
_TOOL_CACHE_LOCK = threading.Lock()

TOOL_INVENTORY: Dict[str, Dict[str, Any]] = {}
_FULL_SCHEMAS: Dict[str, Dict[str, Any]] = {}
TOOL_EXECUTORS: Dict[str, Callable[..., Awaitable[str]]] = {}

_inventory_loaded = False
_registry_lock = threading.RLock()

def parse_sidecar(md_path: Path) -> Optional[Dict[str, Any]]:
    """Parses YAML frontmatter and content from a .md sidecar file."""
    try:
        content = md_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None
        
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
            
        metadata = yaml.safe_load(parts[1])
        instructions = parts[2].strip()
        
        name = metadata.get("name", md_path.stem)

        raw_params = metadata.get("parameters", {})
        cleaned_properties = {
            k: {pk: pv for pk, pv in v.items() if pk != "required"}
            for k, v in raw_params.items()
            if isinstance(v, dict)
        }

        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": metadata.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": cleaned_properties,
                    "required": metadata.get("required", [])
                }
            }
        }
        
        manifest = SkillManifest(
            requires_network=metadata.get("requires_network", False),
            category=metadata.get("category", "general"),
            tier=metadata.get("tier", "common"),
            reversible=metadata.get("reversible", True),
            instructions=instructions,
            cacheable=metadata.get("cacheable", False),
            cache_ttl=int(metadata.get("cache_ttl", 60)),
            risk=str(metadata.get("risk", "low")),
            allowed_tiers=list(metadata.get("allowed_tiers") or []),
        )
        
        return {
            "name": name,
            "schema": schema,
            "manifest": manifest
        }
    except Exception as e:
        logger.error(f"Failed to parse sidecar {md_path}: {e}")
        return None

def register_tool(schema_or_name: "Dict[str, Any] | str | None" = None, manifest: "SkillManifest | None" = None, schema: "Dict[str, Any] | None" = None):
    """Decorator to register a function as a tool with an optional schema and manifest."""
    def decorator(func: Callable[..., Awaitable[str]]):
        with _registry_lock:
            actual_schema_or_name = schema if schema is not None else schema_or_name
            
            if actual_schema_or_name is None:
                logger.error(f"register_tool called without schema or name for {func.__name__}")
                return func

            if isinstance(actual_schema_or_name, str):
                tool_name = actual_schema_or_name
                if tool_name not in TOOL_INVENTORY:
                    TOOL_INVENTORY[tool_name] = {"executor": func}
                else:
                    TOOL_INVENTORY[tool_name]["executor"] = func
                TOOL_EXECUTORS[tool_name] = func
            else:
                tool_name = actual_schema_or_name["function"]["name"]
                _manifest = manifest if manifest is not None else SkillManifest()
                TOOL_INVENTORY[tool_name] = {
                    "schema":   actual_schema_or_name,
                    "executor": func,
                    "manifest": _manifest,
                }
                TOOL_EXECUTORS[tool_name] = func
                _FULL_SCHEMAS[tool_name] = actual_schema_or_name

        return func
    return decorator

def load_all_skills():
    """Scans the skills directory and imports all modules to populate the inventory."""
    global _inventory_loaded
    with _registry_lock:
        if _inventory_loaded:
            return
        _load_skills_unlocked()
        _inventory_loaded = True

def reload_skills():
    """Force a full re-scan and reload of all skill modules."""
    global _inventory_loaded
    with _registry_lock:
        _inventory_loaded = False
        TOOL_INVENTORY.clear()
        _FULL_SCHEMAS.clear()
        TOOL_EXECUTORS.clear()
        _load_skills_unlocked(force_reload=True)
        _inventory_loaded = True

    from app.skills.semantic_router import invalidate_tool_index
    invalidate_tool_index()

def _load_skills_unlocked(force_reload: bool = False):
    """Internal: must be called with _registry_lock held."""
    skills_dir = Path(__file__).parent
    user_skills_dir = Path.home() / ".wade" / "skills"
    
    for search_dir in [skills_dir, user_skills_dir]:
        if not search_dir.is_dir():
            continue
        for md_path in search_dir.rglob("*.md"):
            data = parse_sidecar(md_path)
            if data:
                name = data["name"]
                if name not in TOOL_INVENTORY:
                    TOOL_INVENTORY[name] = {}
                TOOL_INVENTORY[name].update({
                    "schema": data["schema"],
                    "manifest": data["manifest"]
                })
                _FULL_SCHEMAS[name] = data["schema"]

    base_module = "app.skills"
    for file_path in skills_dir.rglob("*.py"):
        if file_path.name in ("__init__.py", "registry.py", "semantic_router.py"):
            continue

        rel_path = file_path.relative_to(skills_dir)
        module_suffix = ".".join(rel_path.with_suffix("").parts)
        module_name = f"{base_module}.{module_suffix}"

        try:
            if module_name in sys.modules:
                if force_reload:
                    importlib.reload(sys.modules[module_name])
            else:
                importlib.import_module(module_name)
        except Exception as e:
            logger.error(f"⚠️ Failed to auto-load skill module '{module_name}': {e}")

    if not user_skills_dir.is_dir():
        return

    for file_path in user_skills_dir.rglob("*.py"):
        if file_path.name.startswith("_"):
            continue

        module_name = f"wade_user_skill.{file_path.stem}"

        try:
            if module_name in sys.modules:
                if force_reload:
                    importlib.reload(sys.modules[module_name])
                continue

            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                logger.warning(f"⚠️ Could not create spec for user skill '{file_path.name}' — skipped")
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module

            logger.warning(
                "[REGISTRY] Loading user skill '%s' into the main process. "
                "User skills are NOT sandboxed and run with full host privileges. "
                "Only install skills you trust.",
                file_path.name,
            )
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error(f"⚠️ Failed to load user skill '{file_path.name}': {e}")

def get_tool_inventory() -> List[str]:
    """Returns a list of available tool names without full schemas."""
    load_all_skills()
    return list(TOOL_INVENTORY.keys())

def get_tool_descriptions() -> List[Dict[str, str]]:
    """Returns a list of tool names and their descriptions for semantic indexing."""
    load_all_skills()
    descriptions = []
    for name, entry in TOOL_INVENTORY.items():
        if "schema" not in entry:
            continue
        desc = entry["schema"]["function"].get("description", "No description available.")
        descriptions.append({
            "name": name,
            "description": desc,
            "category": entry["manifest"].category
        })
    return descriptions

def get_dynamic_tools(requested_tools: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Callable[..., Awaitable[str]]]]:
    """Returns tool schemas and executors for the requested tools, or all if None."""
    load_all_skills()
    
    if requested_tools is None:
        return list(_FULL_SCHEMAS.values()), TOOL_EXECUTORS
    
    selected_schemas = []
    for name in requested_tools:
        if name in _FULL_SCHEMAS:
            selected_schemas.append(_FULL_SCHEMAS[name])
            
    return selected_schemas, TOOL_EXECUTORS

def get_tools_by_manifest(network_available: bool = True, tier: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Callable[..., Awaitable[str]]]]:
    """Returns tool schemas and executors filtered by manifest criteria."""
    load_all_skills()
    selected_schemas: List[Dict[str, Any]] = []
    selected_executors: Dict[str, Callable] = {}

    for name, entry in TOOL_INVENTORY.items():
        if "schema" not in entry:
            continue
        m: SkillManifest = entry.get("manifest", SkillManifest())
        if not network_available and m.requires_network:
            continue
        if tier is not None and m.tier != tier:
            continue
        selected_schemas.append(entry["schema"])
        if "executor" in entry:
            selected_executors[name] = entry["executor"]

    return selected_schemas, selected_executors

def get_all_categories() -> list[str]:
    """Returns sorted list of all distinct skill categories currently registered. Dynamic — new skills auto-appear."""
    load_all_skills()
    return sorted({
        entry["manifest"].category
        for entry in TOOL_INVENTORY.values()
        if "manifest" in entry
    })

def get_tools_by_categories(categories: list[str]) -> list[str]:
    """Returns names of tools whose manifest category is in the given set.

    Only returns tools that have both a schema and a manifest — executor-only
    registrations (added via register_tool("name")) are excluded.
    """
    load_all_skills()
    cats = set(categories)
    return [
        name
        for name, entry in TOOL_INVENTORY.items()
        if "schema" in entry and "manifest" in entry and entry["manifest"].category in cats
    ]

async def execute_tool(tool_name: str, args: Dict[str, Any]) -> str:
    """Dynamically routes a tool call to its registered function, running it safely in a sandbox."""
    load_all_skills()

    executor = TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return f"Error: Unknown tool '{tool_name}'"

    from app.skills.sandbox import run_in_sandbox

    manifest: Optional[SkillManifest] = TOOL_INVENTORY.get(tool_name, {}).get("manifest")
    req_net = manifest.requires_network if manifest else True
    if manifest and manifest.cacheable:
        import json as _json
        cache_key = f"{tool_name}:{_json.dumps(args, sort_keys=True)}"
        now = time.monotonic()
        with _TOOL_CACHE_LOCK:
            entry = _TOOL_CACHE.get(cache_key)
            if entry and entry[1] > now:
                logger.debug("[CACHE] HIT %s (expires in %.0fs)", tool_name, entry[1] - now)
                return entry[0]
        try:
            result = await run_in_sandbox(executor, args, requires_network=req_net)
        except Exception as e:
            return f"Error executing '{tool_name}': {str(e)}"
        with _TOOL_CACHE_LOCK:
            expired = [k for k, v in _TOOL_CACHE.items() if v[1] <= now]
            for k in expired:
                del _TOOL_CACHE[k]
            _TOOL_CACHE[cache_key] = (result, now + manifest.cache_ttl)
        return result

    try:
        return await run_in_sandbox(executor, args, requires_network=req_net)
    except Exception as e:
        return f"Error executing '{tool_name}': {str(e)}"

def get_tool_risk(tool_name: str) -> str:
    """Return the risk level for a registered tool. Defaults to 'low' if untagged or unknown."""
    load_all_skills()
    manifest: Optional[SkillManifest] = TOOL_INVENTORY.get(tool_name, {}).get("manifest")
    if manifest is None:
        return "low"
    return manifest.risk