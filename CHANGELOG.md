# Changelog

All notable changes to W.A.D.E. will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.8-beta] — 2026-05-26

### Added
- Playwright browser binaries are now installed automatically on first `wade start` and `wade setup` — no manual `playwright install chromium` step required. A sentinel file (`~/.wade/.playwright_ready`) is written after a successful install so subsequent starts are instant. Failures warn clearly but never block startup

---

## [0.1.7-beta] — 2026-05-26

### Fixed
- `⚠️ Failed to auto-load skill module 'app.skills.web.browser'` and `app.skills.web.deep_research` warnings on startup — caused by hard top-level `from playwright.async_api import ...` imports that fail when Playwright is not installed, causing the skill registry's eager-loader to log errors for both modules
- `playwright` promoted from optional `[web]` extra to core `dependencies` so `pip install wade-ai` includes it automatically
- `browser.py` import restructured: `from __future__ import annotations` makes type annotations lazy, playwright types moved under `TYPE_CHECKING`, and `async_playwright` uses a soft try/except so the module loads cleanly without playwright installed. `get_page()` raises a clear `RuntimeError` with install instructions if called without the package

---

## [0.1.6-beta] — 2026-05-23

### Fixed
- **403 on every authorization action** — the HITL approve/reject POST (`/api/tasks/{uuid}/approve`) was sending hardcoded `{ 'Content-Type': 'application/json' }` headers, omitting the required `X-WADE-Token` CSRF header. Every authorization attempt was blocked by the CSRF middleware before reaching the handler
- **CSRF race condition on page load** — `_initCsrf()` was called without `await` inside the `DOMContentLoaded` handler, so any request that fired in the first network round-trip had an empty token and would also 403. The handler is now `async` and awaits the token before any other setup function runs

---

## [0.1.5-beta] — 2026-05-23

### Fixed
- UI now renders correctly for users with ad blockers or strict content security policies
- Removed Tailwind CDN (`cdn.tailwindcss.com`) — replaced with a pre-compiled `tailwind.min.css` (30KB) built from the actual classes used in the UI. The CDN was blocked by uBlock Origin, Brave Shields, and SES-hardened environments, which caused `tailwind is not defined` and a completely unstyled page
- Removed Google Fonts CDN (`fonts.googleapis.com`) — blocked by the same tools. Font stacks now fall through to `Inter` if system-installed, then `system-ui` / `Segoe UI` / `Roboto`, and `Cascadia Code` / `Fira Code` / `Consolas` for monospace
- All external network requests eliminated from the UI — W.A.D.E. runs fully offline

---

## [0.1.4-beta] — 2026-05-23

### Fixed
- UI assets (CSS, JS, HTML) now load correctly after pip install — `get_package_dir()` was using `importlib.resources.files("app")` which resolves against `sys.path` and picks up any other `app/` package found first (e.g. Django projects). Replaced with `Path(__file__).resolve().parent.parent` which is anchored to the installed file's physical location
- CSS stylesheet link in `index.html` changed from relative `static/css/style.css` to absolute `/static/css/style.css`, consistent with all JS and icon references

---

## [0.1.3-beta] — 2026-05-23

### Fixed
- `ModuleNotFoundError: No module named 'openwakeword'` (and any remaining voice dep) on fresh pip installs — follow-up to 0.1.2 ensuring the full voice stack is visible on PyPI
- `zeroconf` and `qrcode` added to core `dependencies` in `pyproject.toml`; both were in `requirements.txt` but absent from the pip package metadata entirely

---

## [0.1.2-beta] — 2026-05-23

### Fixed
- `ModuleNotFoundError: No module named 'whisper'` on fresh installs — voice dependencies (`openai-whisper`, `sounddevice`, `kokoro-onnx`, `openwakeword`, `onnxruntime`) promoted from optional `[voice]` extra to core `dependencies` so `pip install wade-ai` includes them automatically
- Voice import failures now surface a clear, actionable error message instead of a raw `ImportError` traceback

---

## [0.1.0-beta] — 2026-05-22

**First public release.** This is the initial beta of W.A.D.E. — a local-first autonomous runtime that runs continuously as a background daemon, processes environmental events proactively, and keeps all data on your hardware.

### Core Runtime
- Orchestrator with task creation, multi-step planning, retry logic, and HITL approval gating
- Executor agent with streaming tool loop, semantic skill routing, and loop-depth guard
- Planner agent with parallel subtask decomposition and final synthesis step
- Critic layer with multi-stage validation (anchor structure → plan → step execution → terminal satisfaction) and automatic escalation to stronger reasoning models on low confidence
- Internal event bus wiring all monitors, agents, and the orchestrator through typed async events
- Context Fusion — grounds planner decisions in real-time system events from the bus

### Inference & Model Routing
- Native HTTP integration for Ollama, OpenAI, Gemini, and Anthropic — no SDK lock-in
- Dynamic model routing via role keys (`fast`, `tools`, `planner`, `reasoner`, `code`, `vision`, `embeddings`)
- Open-ended `provider/model` prefix support for mixing local and cloud models per role
- Setup wizard with hardware detection and Cognition Source selection (Local / Hybrid / Cloud Only)
- Hardware-aware model discovery, ranking, and installation pipeline

### Memory System
- Episodic memory — SQLite-backed conversation store with session isolation
- Semantic memory — ChromaDB vector search for long-term context retrieval
- Passive fact extraction — silent durable fact storage from conversation turns
- Workspace `.md` auto-patch — discovered facts (name, location, timezone, etc.) are written directly into workspace identity files
- Memory compactor — nightly consolidation of episode memory
- Core memory — persistent identity and personality context loaded on every request

### Skills — Web
- `web_search` — DuckDuckGo search with formatted result output
- `deep_research` — multi-source research pipeline
- `control_browser` — full Playwright automation: navigate, click, type, select, screenshot, extract text
- Multi-browser engine selection: Chromium (default), Firefox, WebKit

### Skills — Workspace & Dev
- `read_host_file`, `write_host_file`, `patch_host_file`, `delete_host_file` — host filesystem access
- `scan_directory`, `search_in_files`, `append_workspace_file`, `update_workspace_file`, `delete_workspace_file` — workspace operations
- `git_status`, `git_diff`, `git_commit`, `git_checkout`, `git_branch`, `git_restore` — git tooling
- `map_file_dependencies` — static dependency graph for a codebase
- `run_python` — persistent sandboxed Python REPL
- `run_shell_command` — direct host shell execution
- `run_polyglot` — auto-detect language, compile if needed, execute source files
- `dev_file` — single-file dev skill with syntax checking and auto-fix loop
- `dev_files` — simultaneous multi-file write and execute for interdependent modules
- `code_review` — diff-based architectural, performance, and stylistic review
- `feature_dev` — end-to-end feature development workflow (plan → implement → verify)

### Skills — System
- `check_hardware_stats` — CPU, RAM, GPU, disk metrics
- `check_wade_services_health` — Ollama, ChromaDB, voice, monitor health
- `get_current_time` — timezone-aware time with optional global location lookup
- `hot_reload_system` — live skill reload without daemon restart
- `perform_system_recovery` — system self-repair
- `escalate_cognition` — dynamic upgrade to a stronger reasoning model with hardware safety check
- `analyze_screen` — captures a host monitor screenshot and runs Vision AI analysis

### Skills — Data & Intelligence
- `calculate_math` — complex math with numpy, sympy, scipy, and pandas
- `get_weather` — real-time weather and daily forecast via IP geolocation
- `get_global_recon_intel` — RSS-based global and country news intelligence with volatility index
- `get_aero_flow_telemetry` — real-time ADS-B flight tracking and corporate jet pattern analysis

### Skills — Productivity & Media
- `notion` — full Notion CRUD: pages, databases, blocks, properties
- `schedule_task` — one-time and recurring background job scheduler (natural language + cron)
- `manage_spotify` — Spotify control via media keys and the Spotify Web API

### Skills — Security & Cameras
- `get_home_security_status` — Blink camera system: status, arm/disarm, live snapshot

### Skills — Knowledge Base
- `get_knowledge_inventory` — summary of all indexed files and folders
- `search_indexed_files` — semantic search across indexed files with cross-encoder reranking
- `reset_database` — permanent wipe of ChromaDB vector memory and SQLite indexer state

### Skills — WhatsApp
- `whatsapp_send_message` — send a text message to a phone number or contact JID
- `whatsapp_create_group` — create a group chat with name-resolved participants
- `whatsapp_lookup_contact` — search the synced contact list by name to resolve JIDs

### Proactive Monitoring
- Filesystem monitor — watches configured paths and emits `FS_CHANGE` events
- Schedule monitor — time-based task triggers via APScheduler (cron + interval)
- System monitor — CPU, RAM, and disk threshold alerts with recovery detection
- Proactive monitor — background initiative engine that surfaces alerts to connected clients

### Multi-User & Tier Isolation
- `TierContext` isolation — admin, family, friends, guests, strangers tiers with per-tier system prompts and workspace directories
- `UserRegistry` — resolves WhatsApp JIDs and browser device IDs to tiers via `users.yaml`
- Admin panel UI — user management, conversation history viewer, and device registration

### Security
- All sensitive API routes gated behind `require_admin` with per-request tier resolution
- Sandbox hardening: `RLIMIT` + seccomp BPF blocklist on Linux, Windows Job Objects
- Asset integrity: SHA-256 verification for all downloaded ONNX binaries with atomic rename
- CSRF token middleware on all mutating endpoints (`X-WADE-Token` header)

### UI & Developer Experience
- God Mode HUD — live task traces, tool execution timeline, and model metrics dashboard
- Credentials & Integrations tab — save, clear, and live-test API keys for OpenAI, Anthropic, Gemini, Notion, Blink, and Spotify; keys stored locally, never returned by the API
- Admin panel — tier management, message history, and device registry
- QR code printed at daemon startup for one-scan mobile connection over LAN
- Custom `wade` CLI: `start`, `stop`, `talk`, `fit`, `setup`, `config`, `uninstall`
- `pip install wade-ai` with optional extras: `[llm]`, `[voice]`, `[web]`, `[all]`

---

*This is a beta release. APIs, configuration formats, and skill interfaces may change before v1.0.0.*
