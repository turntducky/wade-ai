# W.A.D.E. — Project Architecture Reference

This document provides a comprehensive overview of the W.A.D.E. codebase, detailing the purpose of each file, its primary connections, and its importance to the overall system. It reflects the state of the project as of **v0.1.0**.

---

## 1. Core Application & Entry Points

| File | Purpose | Primary Connections | Importance |
| :--- | :--- | :--- | :--- |
| `app/main.py` | FastAPI server entry point. Defines REST API (`/api/chat`, `/api/tasks`, `/api/episodes`, `/api/events`, settings, memory, health). Owns the `lifespan` context that boots the inference engine, wires up agents, and starts all monitors. Includes CSRF middleware (`X-WADE-Token` header), first-run onboarding endpoints, and update-check logic. `/health` returns `lan_ip` and `port` consumed by the QR modal. Applies `GZipMiddleware` (≥500 bytes) and mounts static files via `_CachedStaticFiles` (immutable `Cache-Control` for fingerprinted assets, 5-minute TTL for HTML/manifest). Lifespan also calls `planner.set_event_bus(event_bus)` to wire Context Fusion. | `orchestrator.py`, `inference_client.py`, `ollama_manager.py`, `config.py`, `mdns.py` | **Critical** |
| `app/cli.py` | Primary `wade` CLI. Handles `start`, `stop`, `talk`, `fit`, `config`, and diagnostic commands. Supports `--ci` flag on `fit` to skip model downloads in automated environments. `config` allows setting LLM providers and API keys. | `daemon.py`, `voice.py`, `config.py`, `credentials.py` | **Critical** |
| `app/daemon.py` | Manages the Uvicorn background process. Binds to `0.0.0.0` (LAN-accessible). Prefers port 80 with fallback to 8000+. Prints ASCII QR code at startup encoding the LAN IP for one-scan mobile connection. | `main.py`, `workspace.py`, `config.py`, `mdns.py` | **Critical** |
| `app/workspace.py` | Ensures `~/.wade/workspace/` exists and is populated with required template files. Generates initial cognitive architecture. | `config.py`, `main.py`, `cli.py` | **High** |
| `app/setup_wizard.py` | Interactive first-run setup: hardware detection, Cognition Source selection (Local, API Hybrid, Cloud Only), model selection, config generation. Collects API keys for cloud providers. Uses `probe_hardware()` + `select_profile()` from `discovery.py`. Writes `config["llm"]` and `config["models"]`. | `config.py`, `ollama_manager.py`, `hardware.py`, `discovery.py`, `credentials.py` | **High** |

---

## 2. Core Logic & Configuration (`app/core/`)

| File | Purpose | Primary Connections | Importance |
| :--- | :--- | :--- | :--- |
| `app/core/orchestrator.py` | **The Brain.** Central task coordinator. Receives tasks from user chat, monitor daemons, and scheduled jobs. Decomposes via `PlannerAgent`, then dispatches to `ExecutorAgent` through three `CriticAgent` integration points: anchor structure + plan validation pre-execution; per-step `verify_step` with dependency-aware escalation; terminal anchor satisfaction check. `_wave_levels()` groups subtasks by dependency depth for parallel wave execution (string-UUID `depends_on`). Applies `strip_internal_tags()` before every `append_to_memory()` call so streamed `<tool_result>` / `<tool_exec>` markers are never written to conversation history. Fire-and-forget `asyncio.create_task()` calls carry done-callbacks that log exceptions rather than swallowing them silently. Subscribes to the `InternalEventBus` via `subscribe_to_bus()`. | `planner.py`, `executor.py`, `critic.py`, `task_store.py`, `inference_client.py`, `ollama_manager.py`, `events.py` | **Critical** |
| `app/core/events.py` | **Internal Event Bus.** `InternalEventBus` is an asyncio-Queue-backed pub/sub dispatcher. `subscribe(event_type, handler)` registers async handlers; `emit()` / `emit_nowait()` enqueue `WadeEvent` instances; `_dispatch_loop()` drains the queue with a 1-second timeout and calls all registered handlers. Maintains a `_recent: deque(maxlen=20)` rolling buffer of the 20 most recent events. `get_recent_state(n=5)` returns the last *n* events as serializable dicts — consumed by `PlannerAgent` for Context Fusion. | `orchestrator.py`, `planner.py`, all monitor daemons | **High** |
| `app/core/task_store.py` | SQLite-backed `Task` persistence. Every request becomes a `Task` (with `TaskStatus`) that survives restarts and is queryable via `/api/tasks`. Extended with `expected_outcome`, `depends_on: list[str]` (task UUIDs), and terminal failure statuses `INVALID_PLAN`, `GOAL_NOT_SATISFIED`, `TOOL_MISMATCH`. | `orchestrator.py`, `main.py` | **Critical** |
| `app/core/telemetry.py` | **Observability Store.** Append-only SQLite store for God Mode data. Four tables: `tool_traces`, `critic_verdicts`, `inference_metrics`, and `audit_logs`. WAL mode + 5-second busy timeout. Stored at `~/.wade/telemetry.db`. | `orchestrator.py`, `api/v1/godmode.py` | **High** |
| `app/core/config.py` | Centralized configuration management. Single canonical config at `~/.wade/config.yaml`. Supports async and sync access. | All modules | **Critical** |
| `app/core/credentials.py` | **Credentials Manager.** Secure storage and retrieval of API keys and tokens in `~/.wade/credentials.json`. | `inference_client.py`, `notion.py`, `blink.py`, `setup_wizard.py` | **High** |
| `app/core/hitl.py` | **Human-in-the-Loop approval gate.** Module-level `_pending: dict[str, PendingApproval]` holds suspended tool calls. `wait_for_decision()` registers an `asyncio.Event` and cooperatively suspends the executor generator. `APPROVAL_TIMEOUT = 300s` auto-rejects on timeout. | `executor.py`, `api/v1/tasks.py`, `api/v1/sync.py` | **High** |
| `app/core/chroma_utils.py` | ChromaDB utilities with support for multi-provider embeddings. Provides `get_chroma_client()`, `get_collection()`, and `UniversalEmbeddingFunction`. Includes a blocking `_embed_sync_http` fallback for Ollama. | `semantic_memory.py`, `indexer.py` | **High** |
| `app/core/utils.py` | Shared utility helpers. `safe_truncate(text, max_tokens)` prevents context overflow. `strip_internal_tags(text)` removes internal SSE marker tags. | `executor.py`, `orchestrator.py` | **Medium** |
| `app/core/hardware.py` | Detects CPU/GPU/RAM and NPU to optimize model selection. | `setup_wizard.py`, `discovery.py`, `voice.py` | **High** |
| `app/core/personality.py` | Loads AI identity, tone, and system prompt from workspace templates. Tiered file cache: immutable identity files cached for 3600 s; mutable files expire after 30 s. | `executor.py` | **High** |
| `app/core/user_registry.py` | Tracks known users and their preferences for multi-user contexts. Manages device IDs and WhatsApp contact mappings. | `main.py`, `memory_agent.py`, `api/v1/whatsapp.py` | **Medium** |
| `app/core/location.py` | Auto-detects system location via IP. Cached for 6 hours. | `executor.py`, `weather.py` | **Medium** |
| `app/core/mdns.py` | Advertises `wade.local` via mDNS (Zeroconf). Caches the current LAN IP for the QR modal. | `daemon.py`, `main.py` | **Medium** |
| `app/core/scrape.py` | Web content extractor and local model inventory writer. | `web_search.py`, `browser.py`, `model_manager.py` | **Medium** |
| `app/core/security.py` | FastAPI dependency chain for tier enforcement. `get_tier_context()` resolves the caller's tier; `require_admin` rejects non-admin callers. | `main.py`, `api/v1/*` | **High** |
| `app/core/classifier.py` | Classifies user goals into complexity tiers (`simple`, `medium`, `complex`) to determine the processing path. | `orchestrator.py` | **High** |
| `app/core/project_loader.py` | Scans registered directories to identify and load software projects into the cognitive workspace. | `indexer.py`, `workspace.py` | **High** |

---

## 3. Services & Inference (`app/services/`)

| File | Purpose | Primary Connections | Importance |
| :--- | :--- | :--- | :--- |
| `app/services/inference_client.py` | **Multi-Provider Inference Layer.** Dispatches chat and embedding requests to Ollama (local) or OpenAI, Gemini, Anthropic (cloud APIs) using raw `aiohttp`. Supports streaming, non-streaming, and `_metrics_hook`. | `executor.py`, `planner.py`, `memory_agent.py`, `model_router.py`, `telemetry.py`, `credentials.py` | **Critical** |
| `app/services/model_router.py` | Maps role strings (`"tools"`, `"planner"`, etc.) to `ModelRoute` objects. Supports dynamic `provider/model` prefixes. | `inference_client.py`, `config.py` | **Critical** |
| `app/services/ollama_manager.py` | Ollama lifecycle manager. Spawns `ollama serve`, auto-pulls models, and handles health checks/restarts. | `main.py`, `orchestrator.py`, `setup_wizard.py` | **High** |
| `app/services/model_manager.py` | Orchestrates model discovery and installation for `wade fit`. | `discovery.py`, `installer.py`, `config.py` | **High** |
| `app/services/discovery.py` | Live model discovery via ollamadb.dev API. 7-day local cache; offline fallback to curated families. | `hardware.py`, `model_manager.py`, `installer.py` | **High** |
| `app/services/installer.py" | Pulls Ollama models with automatic size fallback. | `model_manager.py`, `discovery.py`, `ollama_manager.py` | **Medium** |
| `app/services/voice.py` | STT (Whisper), TTS (Kokoro-ONNX), and wake-word detection. | `cli.py` | **High** |
| `app/services/messenger.py` | Outbound message interface via WhatsApp (Baileys bridge). | `api/v1/whatsapp.py` | **Medium** |
| `app/services/proactive.py` | Generates unprompted proactive messages; SSE broadcast. Maintains a rolling message buffer for state sync. | `monitors/proactive.py`, `events.py`, `api/v1/sync.py` | **Medium** |

---

## 4. Agent System (`app/agents/`)

All agents use `InferenceClient` and operate on `Task` objects.

| File | Purpose | Importance |
| :--- | :--- | :--- |
| `app/agents/executor.py` | **ExecutorAgent.** Executes single `Task` nodes. Runs tool-call loop (MAX 10 calls, with context-aware loop detection) and streams final responses. Resets and populates `self.traces: list[ToolTrace]` per call. | **Critical** |
| `app/agents/planner.py` | **PlannerAgent.** Decomposes complex goals into `(GoalAnchor, list[Task])`. Supports Context Fusion by injecting recent event bus state into planning. | **High** |
| `app/agents/critic.py` | **CriticAgent.** Stateful constraint-validation system. Validates anchor structure, plan feasibility, step execution (with dependency escalation), and terminal satisfaction. | **High** |
| `app/agents/memory_agent.py` | **MemoryAgent.** Passive fact extraction after each turn and nightly consolidation. | **High** |

### Monitor Daemons (`app/agents/monitors/`)

All monitors are decoupled from `Orchestrator` — they receive an `InternalEventBus` at construction and communicate exclusively through it.

| File | Purpose | Importance |
| :--- | :--- | :--- |
| `app/agents/monitors/base.py` | `MonitorDaemon` base class. Takes `event_bus: InternalEventBus`; provides `emit(event)` helper and `submit_task(goal)` helper. | **High** |
| `app/agents/monitors/proactive.py` | `ProactiveMonitor`. Subscribes to `SYS_THRESHOLD` and `FS_CHANGE` to trigger proactive interventions. | **High** |
| `app/agents/monitors/schedule.py` | `ScheduleMonitor`. APScheduler-backed cron jobs and reminders. | **High** |
| `app/agents/monitors/system.py` | `SystemMonitor`. Polls CPU/RAM/disk every `check_interval` seconds. Emits `SYS_THRESHOLD` events. | **Medium** |
| `app/agents/monitors/filesystem.py` | `FilesystemMonitor`. Watches `~/.wade/workspace/` for external changes via Watchdog. Emits `FS_CHANGE` events. | **Medium** |

---

## 5. Memory Management (`app/memory/`)

| File | Purpose | Importance |
| :--- | :--- | :--- |
| `app/memory/manager.py` | Manages daily markdown conversation logs and history context. | **High** |
| `app/memory/episodes.py` | **`EpisodeStore`.** SQLite-backed episodic memory. Supports semantic search and cleanup. | **High** |
| `app/memory/semantic_memory.py` | Vector-based long-term memory using ChromaDB. Supports semantic purging of both vectors and episodes. | **High** |
| `app/memory/passive_extractor.py` | Identifies durable facts silently from user messages for the structured fact store. | **Medium** |
| `app/memory/compactor.py" | Prunes and summarizes old episodic markdown files to keep history lean. | **Medium** |

---

## 6. Skills & Extensibility (`app/skills/`)

The modular tool system. Tools are registered via JSON schemas and executed at inference time.

| Directory / File | Purpose | Importance |
| :--- | :--- | :--- |
| `app/skills/registry.py` | Skill manifest loader and tool registry. Dynamic discovery and safe loading. Supports sidecar `.md` loading and `@wade_tool` SDK registration. | **Critical** |
| `app/skills/sdk.py` | **Skill SDK.** `@wade_tool` decorator for defining tools inline in Python. Validates schemas at import time. | **High** |
| `app/skills/semantic_router.py` | **Semantic Router.** Selects relevant tools for a goal using vector embeddings. | **High** |
| `app/skills/dev/` | `dev_file.py`, `dev_files.py`, `feature_dev.py`, `code_review.py`. Core software engineering capabilities. | **High** |
| `app/skills/workspace/` | `git.py` (commit/diff), `indexing/query.py`. Local environment manipulation. | **High** |
| `app/skills/web/` | `browser.py` (Playwright), `deep_research.py`. Autonomous web research. | **High** |
| `app/skills/system/` | `diagnostics.py`, `escalate.py`, `hot_reload.py`, `time.py`. | **Medium** |
| `app/skills/math/` | Symbolic and numerical math engine. | **Medium** |
| `app/skills/finance/` | Live market data and analysis via yfinance. | **Medium** |
| `app/skills/notion/` | Comprehensive Notion API integration. | **High** |
| `app/skills/cameras/` | Blink home security camera integration. | **Medium** |
| `app/skills/flights/` | Real-time flight tracking and telemetry. | **Medium** |
| `app/skills/vision/` | Screen capture and vision-based image analysis. | **Medium** |
| `app/skills/news/` | Global news aggregation and summarization. | **Medium** |
| `app/skills/music/` | Spotify playback control and queue management. | **Medium** |
| `app/skills/scheduling/` | Reminder and task scheduling. | **Medium** |

---

## 7. REST API Routers (`app/api/v1/`)

| File | Purpose | Importance |
| :--- | :--- | :--- |
| `app/api/v1/tasks.py` | Task management, filtering, and HITL approval resolution. | **High** |
| `app/api/v1/godmode.py` | **God Mode observability API.** Serves traces, verdicts, and metrics to the HUD. | **High** |
| `app/api/v1/memory.py` | **Memory Management API.** CRUD for structured facts and semantic "forget" commands. | **High** |
| `app/api/v1/sync.py` | **Multi-Device State Sync.** Returns snapshots of active tasks, HITL, and proactive messages. | **Medium** |
| `app/api/v1/whatsapp.py` | FastAPI router handling incoming WhatsApp webhooks and voice messages. | **High** |
| `app/api/v1/admin.py` | Administrative API for system maintenance and user diagnostics. | **Medium** |
| `app/api/v1/credentials.py` | **Credentials & Integrations API.** Service registry + CRUD routes (`GET`, `POST`, `DELETE`) and per-service live connection test endpoints (`POST /{service}/test`) for OpenAI, Anthropic, Gemini, Notion, Blink, and Spotify. Keys are read from `CredentialsManager` at test time — never returned in GET responses. | **High** |

---

## 8. Desktop Shell (`src-tauri/`)

| File | Purpose | Importance |
| :--- | :--- | :--- |
| `src-tauri/src/main.rs` | Tauri v2 entry point. Bridges FastAPI sidecar to desktop. | **Critical** |
| `src-tauri/src/tray.rs` | System tray manager for status and notifications. | **High** |
| `src-tauri/src/quickchat.rs` | Compact global overlay window for rapid interaction. | **High** |

---

## 9. Key Architectural Features

### Multi-Provider Inference
`InferenceClient` (`app/services/inference_client.py`) provides a unified interface for both local (Ollama) and cloud (OpenAI, Gemini, Anthropic) models. Cloud APIs are called directly via raw `aiohttp` to keep dependencies minimal.

### Dynamic Model Routing
`ModelRouter` (`app/services/model_router.py`) handles open-ended `provider/model` prefixes, allowing W.A.D.E. to utilize any new model the moment it is released without requiring a code update.

### Cognition Source Choice
Users can choose between **Local-Only**, **API Hybrid**, and **Cloud-Only** modes during setup. Hybrid mode typically uses local models for chat/fast roles and cloud models for heavy reasoning or vision.

### Monitor Event Bus
All background monitors communicate with the rest of the system through a single `InternalEventBus` (`app/core/events.py`). Monitors emit typed `WadeEvent` instances; the orchestrator and `ProactiveEngine` subscribe to the events they care about.

### Critic Layer
A stateful constraint-validation system threaded through the multi-step pipeline. `CriticAgent` validates goals, plans, and individual steps, with dependency-aware escalation to higher-capability models (like Reasoners) when confidence is low or failures occur.

### Context Fusion
`PlannerAgent` injects the most recent system events from the bus into its planning context, ensuring that W.A.D.E.'s plans are grounded in real-time system state (e.g., resource alerts, file changes).

### God Mode Observability
A developer HUD in the Web UI surfacing live task graphs, critic verdicts, and performance metrics. Data is persistent across restarts in `telemetry.db`.

### Skill SDK
`@wade_tool` in `app/skills/sdk.py` enables entirely Python-driven tool definitions with automatic schema generation and import-time validation.

---

## 10. Importance Key

| Level | Meaning |
| :--- | :--- |
| **Critical** | Core dependency; system fails without it. |
| **High** | Essential for primary features. |
| **Medium** | Advanced skills or optimizations. |
| **Low** | Optional utilities. |