# Changelog

All notable changes to W.A.D.E. will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
