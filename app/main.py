import re
import sys
import uuid
import json
import logging
import secrets
import asyncio
import importlib
import subprocess

from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from logging.handlers import RotatingFileHandler
from fastapi.middleware.gzip import GZipMiddleware
from fastapi import FastAPI, HTTPException, Request, Body, Depends
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, Response

from app.agents.planner import PlannerAgent
from app.core.events import InternalEventBus
from app.agents.executor import ExecutorAgent
from app.core.orchestrator import orchestrator
from app.agents.memory_agent import MemoryAgent
from app.workspace import ensure_workspace_exists
from app.memory.episodes import get_episode_store
from app.api.v1.sync import router as sync_router
from app.api.v1.admin import router as admin_router
from app.api.v1.tasks import router as tasks_router
from app.agents.monitors.base import MonitorRegistry
from app.agents.monitors.system import SystemMonitor
from app.api.v1.config import router as config_router
from app.services.ollama_manager import ollama_manager
from app.api.v1.godmode import router as godmode_router
from app.agents.monitors.schedule import ScheduleMonitor
from app.api.v1.whatsapp import router as whatsapp_router
from app.agents.monitors.build_logs import BuildLogMonitor
from app.api.v1.memory import router as memory_facts_router
from app.agents.monitors.filesystem import FilesystemMonitor
from app.api.v1.blink_auth import router as blink_auth_router
from app.core.security import get_tier_context, require_admin
from app.api.v1.credentials import router as credentials_router
from app.memory.manager import load_recent_memory, clear_memory
from app.api.v1.spotify_auth import router as spotify_auth_router
from app.skills.news.global_news import execute_get_global_recon_intel
from app.skills.flights.aero_flow import execute_get_aero_flow_telemetry
from app.skills.cameras.blink import execute_get_home_security_status, get_camera_image_bytes
from app.skills.registry import load_all_skills
from app.core.config import ConfigManager, CONFIG_FILE, LOG_FILE, get_package_dir, WORKSPACE_DIR
from app.agents.monitors.proactive import proactive_monitor, proactive_monitor as proactive_engine
from app.services.inference_client import inference_client, close_session as _close_inference_session
try:
    from app.skills.indexing.indexer import start_live_indexer as _start_live_indexer, stop_live_indexer as _stop_live_indexer
    async def start_live_indexer(): _start_live_indexer()
    async def stop_live_indexer(): _stop_live_indexer()
except (ImportError, AttributeError):
    async def start_live_indexer(): pass
    async def stop_live_indexer(): pass

MAX_SCRIPT_WORKERS = 8
SCRIPT_TIMEOUT = 10.0
BASE_DIR = get_package_dir()

logger = logging.getLogger("wade")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    sh = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh.setFormatter(formatter)
    sh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(sh)

if sys.platform == "win32":
    try:
        mod = importlib.import_module("asyncio.windows_events")
        WindowsSelectorEventLoopPolicy = getattr(mod, "WindowsSelectorEventLoopPolicy", None)
    except Exception:
        WindowsSelectorEventLoopPolicy = None

    if WindowsSelectorEventLoopPolicy is not None:
        asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())

active_tasks = set()
EXECUTION_LIMIT = asyncio.Semaphore(MAX_SCRIPT_WORKERS)
monitor_registry = MonitorRegistry()
_telemetry = None
_skills_ready: asyncio.Event = asyncio.Event()
_skills_error: str | None = None

def _get_preload_router():
    """Returns the cached SkillRouter singleton from executor — patchable for tests."""
    from app.agents.executor import _get_skill_router
    return _get_skill_router()

_PRELOAD_TIMEOUT = 30.0

async def _preload_skills():
    """Background task: loads all skill modules and warms the ChromaDB semantic index."""
    global _skills_error
    try:
        async def _do_preload():
            await asyncio.to_thread(load_all_skills)
            router = _get_preload_router()
            await asyncio.to_thread(router.index_tools)

        await asyncio.wait_for(_do_preload(), timeout=_PRELOAD_TIMEOUT)
        _skills_ready.set()
        logger.info("Skills preloaded and semantic index warmed.")
    except asyncio.TimeoutError:
        logger.error("Skills preload timed out after %.0fs.", _PRELOAD_TIMEOUT)
        _skills_error = f"Preload timed out after {_PRELOAD_TIMEOUT:.0f}s"
    except Exception as e:
        logger.error("Skills preload failed: %s", e)
        _skills_error = str(e)

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_workspace_exists()
    logger.info("W.A.D.E. 2.0 booting...")

    try:
        await ollama_manager.ensure_running()
    except RuntimeError as e:
        logger.error("Ollama startup failed: %s", e)
        logger.warning("Proceeding without Ollama — inference will be unavailable.")

    planner = PlannerAgent(inference_client)
    orchestrator.set_planner(planner)
    orchestrator.set_executor_cls(ExecutorAgent)
    memory_agent = MemoryAgent(client=inference_client, episode_store=get_episode_store())
    orchestrator.set_memory_agent(memory_agent)

    from app.core.telemetry import TelemetryStore, TELEMETRY_DB_PATH
    import app.services.inference_client as _ic_module

    global _telemetry
    _telemetry = TelemetryStore(TELEMETRY_DB_PATH)
    orchestrator.set_telemetry(_telemetry)

    async def _metrics_hook(role: str, model: str, pt: int, ct: int, lat: int) -> None:
        if _telemetry is not None:
            await asyncio.to_thread(_telemetry.record_metric, role, model, pt, ct, lat)

    _ic_module.set_metrics_hook(_metrics_hook)

    event_bus = InternalEventBus()
    orchestrator.subscribe_to_bus(event_bus)
    planner.set_event_bus(event_bus)
    proactive_monitor.__init__(event_bus, task_store=orchestrator._store)
    proactive_monitor.set_inference_fn(orchestrator.process)

    await start_live_indexer()

    preload_task = asyncio.create_task(_preload_skills())
    active_tasks.add(preload_task)
    preload_task.add_done_callback(active_tasks.discard)
    logger.info("Skills preload task launched in background.")

    config  = ConfigManager.get()
    sys_cfg = config.get("monitors", {}).get("system", {})
    schedule_monitor   = ScheduleMonitor(event_bus)
    system_monitor     = SystemMonitor(
        event_bus,
        cpu_threshold  = float(sys_cfg.get("cpu_threshold",  85.0)),
        ram_threshold  = float(sys_cfg.get("ram_threshold",  90.0)),
        disk_threshold = float(sys_cfg.get("disk_threshold", 95.0)),
    )
    filesystem_monitor = FilesystemMonitor(event_bus)
    build_log_monitor = BuildLogMonitor(event_bus, WORKSPACE_DIR)
    monitor_registry.register(proactive_monitor)
    monitor_registry.register(schedule_monitor)
    monitor_registry.register(system_monitor)
    monitor_registry.register(filesystem_monitor)
    monitor_registry.register(build_log_monitor)
    schedule_monitor.add_job(
        goal="__nightly_consolidation__",
        trigger="cron",
        hour=0,
        minute=5,
    )

    HEARTBEAT_PROMPT = (
        "[SYSTEM HEARTBEAT TRIGGER] Read HEARTBEAT.md if it exists (workspace context). "
        "Follow it strictly. Do not infer or repeat old tasks from prior chats. "
        "If nothing needs attention, reply exactly with: HEARTBEAT_OK"
    )

    schedule_monitor.add_job(
        goal=HEARTBEAT_PROMPT,
        trigger="interval",
        minutes=30,
    )

    for monitor in monitor_registry.all():
        task = asyncio.create_task(monitor.run())
        active_tasks.add(task)
        logger.info("Started monitor: %s", monitor.name)

    bus_task = asyncio.create_task(event_bus.start())
    active_tasks.add(bus_task)
    logger.info("InternalEventBus started.")

    from app.core.mdns import start_mdns, stop_mdns
    _port = ConfigManager.get().get("port", 8000)
    _lan_ip = await asyncio.to_thread(start_mdns, _port)
    _host_url = f"http://wade.local/ui" if _port == 80 else f"http://wade.local:{_port}/ui"
    _lan_url  = f"http://{_lan_ip}/ui"  if _port == 80 else f"http://{_lan_ip}:{_port}/ui"
    logger.info("W.A.D.E. reachable on LAN → %s  (%s)", _host_url, _lan_url)

    yield

    logger.info("W.A.D.E. shutting down...")
    await asyncio.to_thread(stop_mdns)
    await stop_live_indexer()

    event_bus.stop()

    for task in list(active_tasks):
        task.cancel()
    if active_tasks:
        await asyncio.gather(*active_tasks, return_exceptions=True)

    await _close_inference_session()
    await ollama_manager.shutdown()

app = FastAPI(title="W.A.D.E. Gateway", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1500)

_CSRF_TOKEN = secrets.token_hex(32)

@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    """Block state-changing requests that lack the session CSRF token."""
    from fastapi.responses import JSONResponse as _JSONResponse
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if not request.url.path.startswith("/api/v1/whatsapp"):
            token = request.headers.get("X-WADE-Token", "")
            if token != _CSRF_TOKEN:
                return _JSONResponse({"detail": "Invalid or missing CSRF token"}, status_code=403)
    try:
        return await call_next(request)
    except Exception as exc:
        logger.error("Unhandled exception in request handler [%s %s]: %s", request.method, request.url.path, exc, exc_info=True)
        return _JSONResponse({"detail": "Internal server error"}, status_code=500)

@app.get("/api/csrf-token")
async def get_csrf_token():
    """Returns the session CSRF token. Frontend must fetch this on load."""
    return {"token": _CSRF_TOKEN}

app.include_router(whatsapp_router)
app.include_router(admin_router)
app.include_router(godmode_router)
app.include_router(tasks_router)
app.include_router(memory_facts_router)
app.include_router(sync_router)
app.include_router(credentials_router)
app.include_router(config_router)
app.include_router(spotify_auth_router)
app.include_router(blink_auth_router)

@app.get("/api/user/profile")
async def get_user_profile():
    name = ConfigManager.get_user_name()
    return {"status": "success", "name": name}

_STATIC_DIR = get_package_dir() / "static"

class _CachedStaticFiles(StaticFiles):
    """Serve JS/CSS with no-cache (force ETag re-validation); other assets get 1-hour caching."""
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.endswith(('.js', '.css')):
            response.headers['Cache-Control'] = 'no-cache'
        elif not path.endswith('.html'):
            response.headers['Cache-Control'] = 'public, max-age=3600'
        return response

app.mount("/static", _CachedStaticFiles(directory=str(_STATIC_DIR)), name="static")

@app.get("/api/version")
async def get_version():
    from app.core.version import VERSION, VERSION_LABEL
    return {"version": VERSION, "label": VERSION_LABEL}

@app.get("/health")
async def health():
    from app.core.mdns import get_cached_lan_ip
    port = ConfigManager.get().get("port", 8000)
    return {"status": "ok", "lan_ip": get_cached_lan_ip(), "port": port}

@app.get("/api/ready")
async def readiness():
    """Returns whether skills are loaded and the semantic index is warm."""
    return {"ready": _skills_ready.is_set(), "error": _skills_error}

@app.get("/")
async def root():
    return {"status": "online", "system": "W.A.D.E. Gateway - Headless Mode"}

@app.get("/ui", response_class=HTMLResponse)
async def ui_page():
    return FileResponse(str(_STATIC_DIR / "html" / "index.html"))

@app.get("/api/events")
async def sse_events(request: Request):
    """SSE endpoint for streaming real-time events to the frontend dashboard."""
    client_queue: asyncio.Queue = asyncio.Queue()

    async def stream():
        await proactive_engine.register(client_queue)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # SR-stable-timeout: Replace asyncio.wait_for with ensure_future + wait
                    _fut = asyncio.ensure_future(client_queue.get())
                    done, _ = await asyncio.wait([_fut], timeout=25.0)
                    if not done:
                        _fut.cancel()
                        try:
                            await _fut
                        except (asyncio.CancelledError, Exception):
                            pass
                        raise asyncio.TimeoutError()
                    event = _fut.result()
                    payload = json.dumps(event)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await proactive_engine.unregister(client_queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })

async def run_native_script(script_code: str) -> str:
    async with EXECUTION_LIMIT:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", script_code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                _fut = asyncio.ensure_future(proc.communicate())
                done, _ = await asyncio.wait([_fut], timeout=SCRIPT_TIMEOUT)
                if not done:
                    _fut.cancel()
                    try:
                        await _fut
                    except (asyncio.CancelledError, Exception):
                        pass
                    raise asyncio.TimeoutError()
                stdout, stderr = _fut.result()
                return stdout.decode().strip() or stderr.decode().strip()
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"Error: Script execution timed out after {SCRIPT_TIMEOUT} seconds."
        except Exception as e:
            return f"Execution failed: {e}"

class ChatRequest(BaseModel):
    prompt: str
    session_id: str | None = None

class StopChatRequest(BaseModel):
    session_id: str

class SettingsUpdateRequest(BaseModel):
    provider: str | None = None
    chat_model: str | None = None

class SkillCreateRequest(BaseModel):
    skill_name: str
    code: str

class ScriptRequest(BaseModel):
    script_code: str

class SecurityActionRequest(BaseModel):
    action: Literal["arm", "disarm", "snap", "status"]
    camera_name: str | None = None

class ScheduleJobRequest(BaseModel):
    goal: str
    trigger: str
    value: str

class FilesystemPathRequest(BaseModel):
    path: str

class ProactiveSettingsRequest(BaseModel):
    cooldown_minutes: int = Field(ge=1, le=1440)
    idle_check_minutes: int = Field(ge=1, le=1440)
    max_per_hour: int = Field(ge=1, le=60)

class ProactiveFeedbackRequest(BaseModel):
    message_id: str
    signal: str = Field(pattern="^(engaged|ignored|dismissed)$")

class ProactiveSuppressRequest(BaseModel):
    topic: str

@app.post("/api/chat")
async def api_chat(req: ChatRequest, request: Request, tier_ctx=Depends(get_tier_context)):
    session_id = req.session_id or str(uuid.uuid4())
    proactive_engine.notify_user_active()
    response_stream = orchestrator.process(req.prompt, session_id=session_id, tier_ctx=tier_ctx)
    return StreamingResponse(response_stream, media_type="text/event-stream")

@app.post("/api/chat/stop")
async def api_chat_stop(req: StopChatRequest, _: object = Depends(get_tier_context)):
    success = orchestrator.cancel_session(req.session_id)
    return {"status": "success" if success else "not_found"}

@app.post("/api/execute")
async def execute_script(req: ScriptRequest, _: object = Depends(require_admin)):
    result = await run_native_script(req.script_code)
    return {"status": "success", "output": result}

@app.get("/api/settings")
async def get_settings():
    config = ConfigManager.get()
    return {
        "status": "success",
        "user_name": ConfigManager.get_user_name(),
        "provider": config.get("llm", {}).get("provider", "ollama"),
        "monitors": config.get("monitors", {}),
        "indexer": config.get("indexer", {
            "enabled_zones": ["core", "system", "projects"],
            "custom_dirs": []
        }),
        "full_config": config
    }

@app.post("/api/settings")
async def update_settings(req: Request):
    data = await req.json()
    config = ConfigManager.get()
    
    if "user_name" in data:
        config["user_name"] = data["user_name"]
    
    if "provider" in data:
        config.setdefault("llm", {})["provider"] = data["provider"]
        
    if "monitors" in data:
        config["monitors"] = data["monitors"]

    if "indexer" in data:
        config["indexer"] = data["indexer"]

    ConfigManager.save(config)
    return {"status": "success", "message": "Settings updated."}

@app.post("/api/indexer/rebuild")
async def rebuild_indexer():
    """Wipes the current vector index and state DB, then triggers a fresh background sync."""
    try:
        from app.skills.indexing.indexer import STATE_DB_PATH, CHROMA_DB_DIR
        import shutil
        import os

        await stop_live_indexer()

        if CHROMA_DB_DIR.exists():
            shutil.rmtree(CHROMA_DB_DIR, ignore_errors=True)
            CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)
        
        if STATE_DB_PATH.exists():
            try:
                os.remove(STATE_DB_PATH)
            except OSError:
                pass

        await start_live_indexer()
        
        return {"status": "success", "message": "Index wipe initiated. Rebuilding in background..."}
    except Exception as e:
        logger.error("Failed to rebuild index: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/settings/models")
async def get_model_routing():
    config = ConfigManager.get()
    from app.services.model_router import DEFAULT_ROUTING_TABLE
    mapping = {**DEFAULT_ROUTING_TABLE, **config.get("models", {})}
    return {"status": "success", "routing": mapping}

@app.post("/api/settings/models")
async def update_model_routing(update: dict[str, str] = Body(...)):
    clean = {k.strip(): v.strip() for k, v in update.items() if k.strip() and v.strip()}
    config = ConfigManager.get()
    config["models"] = clean
    ConfigManager.save(config)
    return {"status": "success", "message": "Model routing saved."}

@app.get("/api/settings/skill-categories")
async def get_skill_categories():
    """Returns all currently registered skill categories. Dynamic — updates as new skills are added."""
    from app.skills.registry import get_all_categories
    return {"categories": get_all_categories()}

@app.get("/api/settings/tier-permissions")
async def get_tier_permissions():
    """Returns current per-tier skill category allowlists (config overrides or built-in defaults)."""
    from app.core.user_registry import _TIER_TOOLS
    config = ConfigManager.get()
    overrides = config.get("tier_permissions", {})
    non_admin = ("family", "friends", "guests", "strangers")
    result = {}
    for tier in non_admin:
        if tier in overrides and isinstance(overrides[tier], list):
            result[tier] = overrides[tier]
        else:
            result[tier] = list(_TIER_TOOLS.get(tier, []))
    return {"permissions": result}

@app.post("/api/settings/tier-permissions")
async def save_tier_permissions(request: Request):
    """Persist per-tier skill category allowlists to config.yaml."""
    data = await request.json()
    from app.skills.registry import get_all_categories
    valid_cats = set(get_all_categories())
    cleaned = {
        tier: [c for c in cats if c in valid_cats]
        for tier, cats in data.items()
        if isinstance(cats, list)
    }
    config = ConfigManager.get()
    config["tier_permissions"] = cleaned
    ConfigManager.save(config)
    from app.core.user_registry import user_registry
    user_registry._loaded_at = 0.0
    return {"status": "success"}

def _write_projects_md(projects: list) -> None:
    lines = [
        "# CODE PROJECTS",
        "",
        "*W.A.D.E. reads this file on every request to understand your active codebases.*",
        "",
        "---",
        "",
    ]
    for p in projects:
        name = p.get("name", "Unnamed Project")
        lines.append(f"### {name}")
        lines.append("")
        if p.get("path"):    lines.append(f"- **Path:** `{p['path']}`")
        if p.get("stack"):   lines.append(f"- **Stack:** {p['stack']}")
        if p.get("purpose"): lines.append(f"- **Purpose:** {p['purpose']}")
        if p.get("status"):  lines.append(f"- **Status:** {p['status']}")
        if p.get("notes"):   lines.append(f"- **Notes:** {p['notes']}")
        lines.append("")
        lines.append("---")
        lines.append("")
    (WORKSPACE_DIR / "PROJECTS.md").write_text("\n".join(lines), encoding="utf-8")

@app.get("/api/settings/projects")
async def get_projects():
    config = ConfigManager.get()
    return {"status": "success", "projects": config.get("projects", [])}

@app.post("/api/settings/projects")
async def save_projects(request: Request):
    data = await request.json()
    projects = data.get("projects", [])
    if not isinstance(projects, list):
        return {"status": "error", "message": "Invalid projects data."}
    config = ConfigManager.get()
    config["projects"] = projects
    ConfigManager.save(config)
    await asyncio.to_thread(_write_projects_md, projects)
    return {"status": "success"}

_WORKSPACE_EDITABLE = {"PROJECTS.md", "USER.md", "BUSINESS.md", "HEARTBEAT.md"}

@app.get("/api/workspace/file")
async def get_workspace_file(name: str):
    if name not in _WORKSPACE_EDITABLE:
        raise HTTPException(status_code=403, detail="File not editable.")
    path = WORKSPACE_DIR / name
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"status": "success", "content": content, "name": name}

@app.post("/api/workspace/file")
async def save_workspace_file(request: Request):
    data = await request.json()
    name = data.get("name", "")
    content = data.get("content", "")
    if name not in _WORKSPACE_EDITABLE:
        raise HTTPException(status_code=403, detail="File not editable.")
    if not isinstance(content, str):
        return {"status": "error", "message": "Invalid content."}
    (WORKSPACE_DIR / name).write_text(content, encoding="utf-8")
    return {"status": "success"}

@app.get("/api/memory")
async def get_memory(_: object = Depends(require_admin)):
    history = load_recent_memory(max_chars=100000)
    if "*No previous conversation history" in history:
        return {"status": "success", "history": ""}
    return {"status": "success", "history": history}

@app.delete("/api/memory")
async def delete_memory(_: object = Depends(require_admin)):
    success = clear_memory()
    return {"status": "success" if success else "error", "message": "Memory session cleared." if success else "Failed to clear memory file."}

@app.delete("/api/memory/truncate/{index}")
async def api_truncate_memory(index: int, tier_ctx=Depends(get_tier_context)):
    from app.memory.manager import truncate_memory_at
    success = truncate_memory_at(index)
    return {"status": "success" if success else "error"}

@app.get("/api/tasks")
async def get_tasks(limit: int = 50):
    tasks = orchestrator.list_recent_tasks(limit=limit)
    return {
        "status": "success",
        "tasks": [
            {
                "id":           t.id,
                "goal":         t.goal,
                "status":       t.status.value,
                "created_by":   t.created_by,
                "created_at":   t.created_at.isoformat(),
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "result":       t.result,
            }
            for t in tasks
        ],
    }

@app.get("/api/monitors")
async def get_monitors():
    return {
        "status": "success",
        "monitors": monitor_registry.status(),
    }

@app.get("/api/models/status")
async def get_model_status(tier_ctx=Depends(get_tier_context)):
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["ollama", "list"], capture_output=True, text=True, timeout=5
        )
        installed = result.stdout.lower()
    except Exception:
        installed = ""
    cfg = ConfigManager.get().get("models", {})
    return {"models": {name: (name.lower() in installed) for name in cfg.values() if name}}

@app.get("/api/monitors/schedule/jobs")
async def list_schedule_jobs(_: object = Depends(require_admin)):
    mon = monitor_registry.get("schedule")
    if not isinstance(mon, ScheduleMonitor):
        raise HTTPException(status_code=503, detail="Schedule monitor not running")
    return {"jobs": mon.list_jobs()}

@app.post("/api/monitors/schedule/jobs")
async def add_schedule_job(req: ScheduleJobRequest, _: object = Depends(require_admin)):
    mon = monitor_registry.get("schedule")
    if not isinstance(mon, ScheduleMonitor):
        raise HTTPException(status_code=503, detail="Schedule monitor not running")
    try:
        if req.trigger == "cron":
            h, m = req.value.split(":")
            job_id = mon.add_job(req.goal, "cron", hour=int(h), minute=int(m))
        elif req.trigger == "interval":
            val = req.value.strip().lower()
            if val.endswith("h"):
                n = int(val[:-1])
                if n < 1:
                    raise ValueError("Interval hours must be at least 1")
                job_id = mon.add_job(req.goal, "interval", hours=n)
            elif val.endswith("m"):
                n = int(val[:-1])
                if n < 1:
                    raise ValueError("Interval minutes must be at least 1")
                job_id = mon.add_job(req.goal, "interval", minutes=n)
            else:
                raise ValueError("Interval must end with h or m")
        else:
            raise ValueError(f"Unknown trigger: {req.trigger}")
        return {"status": "success", "job_id": job_id}
    except (ValueError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/monitors/schedule/jobs/{job_id}")
async def delete_schedule_job(job_id: str, _: object = Depends(require_admin)):
    mon = monitor_registry.get("schedule")
    if not isinstance(mon, ScheduleMonitor):
        raise HTTPException(status_code=503, detail="Schedule monitor not running")
    try:
        mon.remove_job(job_id)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.patch("/api/monitors/filesystem/path")
async def update_filesystem_path(req: FilesystemPathRequest, _: object = Depends(require_admin)):
    mon = monitor_registry.get("filesystem")
    if not isinstance(mon, FilesystemMonitor):
        raise HTTPException(status_code=503, detail="Filesystem monitor not running")
    
    new_path = Path(req.path).expanduser().resolve()
    if not new_path.exists() or not new_path.is_dir():
        raise HTTPException(status_code=400, detail="Path does not exist or is not a directory")
    
    mon.change_watch_dir(new_path)
    return {"status": "success", "watch_path": str(new_path)}

@app.patch("/api/monitors/proactive/settings")
async def update_proactive_settings(req: ProactiveSettingsRequest, _: object = Depends(require_admin)):
    import app.services.proactive as proactive_module
    proactive_module.COOLDOWN_MINUTES     = req.cooldown_minutes
    proactive_module.IDLE_CHECK_MINUTES   = req.idle_check_minutes
    proactive_module.MAX_PER_HOUR         = req.max_per_hour
    return {"status": "success"}

@app.post("/api/proactive/feedback")
async def proactive_feedback(req: ProactiveFeedbackRequest):
    from app.services.proactive import proactive_engine as _pe
    _pe.record_feedback(req.message_id, req.signal)
    return {"status": "success"}

@app.get("/api/proactive/preferences")
async def get_proactive_preferences(_: object = Depends(require_admin)):
    from app.services import proactive_prefs
    prefs = proactive_prefs.load()
    return {
        "suppressed":  prefs.get("suppressed", []),
        "engagement":  prefs.get("engagement", {}),
    }

@app.post("/api/proactive/suppress")
async def suppress_proactive_topic(req: ProactiveSuppressRequest, _: object = Depends(require_admin)):
    from app.services import proactive_prefs
    proactive_prefs.suppress(req.topic)
    return {"status": "success", "suppressed": req.topic}

@app.delete("/api/proactive/suppress/{topic}")
async def unsuppress_proactive_topic(topic: str, _: object = Depends(require_admin)):
    from app.services import proactive_prefs
    proactive_prefs.unsuppress(topic)
    return {"status": "success", "unsuppressed": topic}

@app.get("/api/episodes")
async def get_episodes(limit: int = 100, episode_type: str | None = None, _: object = Depends(require_admin)):
    store = get_episode_store()
    if episode_type:
        episodes = store.get_by_type(episode_type, limit=limit)
    else:
        episodes = store.query_recent(limit=limit)
    return {
        "status": "success",
        "episodes": [
            {
                "id":         e.id,
                "type":       e.type,
                "content":    e.content,
                "timestamp":  e.timestamp.isoformat(),
                "session_id": e.session_id,
                "tags":       e.tags,
            }
            for e in episodes
        ],
    }

@app.delete("/api/episodes/{episode_id}")
async def delete_episode(episode_id: str, _: object = Depends(require_admin)):
    store = get_episode_store()
    try:
        store.delete(episode_id)
        return {"status": "success"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Episode not found")

@app.post("/api/skills")
async def create_skill(req: SkillCreateRequest, _: object = Depends(require_admin)):
    safe_name = re.sub(r'[^a-z0-9_]', '', req.skill_name.lower())
    if not safe_name or safe_name in ["__init__", "base", "main"]:
        return {"status": "error", "message": "Invalid or reserved skill name."}
    
    custom_skills_dir = BASE_DIR / "skills" / "custom"
    custom_skills_dir.mkdir(parents=True, exist_ok=True)
    
    init_file = custom_skills_dir / "__init__.py"
    if not init_file.exists():
        init_file.touch()
        
    skill_file = custom_skills_dir / f"{safe_name}.py"
    try:
        skill_file.write_text(req.code, encoding="utf-8")
        return {"status": "success", "message": f"Skill saved as {safe_name}.py"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to save skill: {e}"}

@app.get("/api/security")
async def get_security(_: object = Depends(require_admin)):
    try:
        raw = await execute_get_home_security_status(action="status")
        payload = json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    if payload.get("status") == "error":
        raise HTTPException(status_code=502, detail=payload.get("message", "Skill error"))
    return payload

@app.post("/api/security")
async def post_security(req: SecurityActionRequest, _: object = Depends(require_admin)):
    try:
        raw = await execute_get_home_security_status(
            action=req.action, camera_name=req.camera_name
        )
        payload = json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    if payload.get("status") == "error":
        raise HTTPException(status_code=502, detail=payload.get("message", "Skill error"))
    return payload

@app.get("/api/security/image/{camera_name}")
async def get_camera_image(camera_name: str, _: object = Depends(require_admin)):
    try:
        data = await get_camera_image_bytes(camera_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not data:
        raise HTTPException(status_code=404, detail="Image not available")
    return Response(content=data, media_type="image/jpeg")

@app.get("/api/recon")
async def get_recon():
    try:
        raw = await execute_get_global_recon_intel(scope="global")
        payload = json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    if payload.get("status") == "error":
        raise HTTPException(status_code=502, detail=payload.get("message", "Skill error"))
    return payload

@app.get("/api/aero")
async def get_aero():
    try:
        raw = await execute_get_aero_flow_telemetry(scope="global")
        payload = json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    if payload.get("status") == "error":
        raise HTTPException(status_code=502, detail=payload.get("message", "Skill error"))
    return payload

@app.get("/api/onboarding/status")
async def onboarding_status():
    """Returns whether first-run setup is needed."""
    configured = CONFIG_FILE.exists() and ConfigManager.is_configured()
    ollama_ready = await ollama_manager.is_running()
    config = ConfigManager.get() if configured else {}
    return {
        "needs_onboarding": not configured,
        "ollama_ready": ollama_ready,
        "name_set": bool(config.get("user_name")),
    }

@app.post("/api/onboarding/complete")
async def onboarding_complete(request: Request):
    """Saves the user name collected during onboarding."""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    config = ConfigManager.get()
    config["user_name"] = name
    ConfigManager.save(config)

    from app.core.config import WORKSPACE_DIR
    user_md = WORKSPACE_DIR / "USER.md"
    content = user_md.read_text(encoding="utf-8") if user_md.exists() else ""
    content = re.sub(r"(?m)^Name:.*$", f"Name: {name}", content)
    if "Name:" not in content:
        content = f"Name: {name}\n" + content
    user_md.write_text(content, encoding="utf-8")

    return {"status": "ok", "name": name}

@app.get("/api/update-check")
async def check_for_update():
    """Checks the latest GitHub Release tag against the running version."""
    import httpx
    from importlib.metadata import version as pkg_version, PackageNotFoundError

    try:
        current = pkg_version("wade-ai")
    except PackageNotFoundError:
        current = "0.0.0"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.github.com/repos/turntducky/wade-ai/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            data = resp.json()
            latest_tag = data.get("tag_name", "").lstrip("v")
            return {
                "current": current,
                "latest": latest_tag,
                "update_available": latest_tag != "" and latest_tag != current,
                "release_url": data.get("html_url", ""),
            }
    except Exception:
        return {"current": current, "latest": None, "update_available": False, "release_url": ""}