# W.A.D.E. Development Roadmap

**Last updated:** 2026-05-20

---

## Completed

### Foundation & Architecture
- [x] Core orchestrator — task creation, retry logic, multi-step planning, approval gating
- [x] Executor agent — streaming tool loop, semantic skill routing, MAX_TOOL_CALLS guard, loop detection
- [x] Planner agent — subtask decomposition via `asyncio.gather`, synthesis step
- [x] Inference client — model routing (Ollama / cloud), streaming and non-streaming modes
- [x] Multi-Provider LLM Support — native HTTP integration for OpenAI, Gemini, and Anthropic
- [x] Dynamic Model Routing — open-ended `provider/model` prefix support
- [x] Context Fusion — grounds planning in real-time system events from the internal bus
- [x] Skill registry — `@register_tool` decorator, `@wade_tool` SDK, YAML sidecar auto-discovery
- [x] Semantic router — ChromaDB-backed skill selection by task goal
- [x] Setup wizard — hardware detection and Cognition Source selection (Local, Hybrid, Cloud)
- [x] Daemon / PID management — single-instance enforcement, graceful shutdown
- [x] Dynamic model discovery — hardware-aware model ranking and installation pipeline
- [x] QR code mobile access — ASCII QR printed at daemon startup for mobile connection

### Memory System
- [x] Episode memory — SQLite-backed conversation store with session isolation
- [x] Semantic memory — ChromaDB vector search for long-term context retrieval
- [x] Passive extraction — silent durable fact storage from conversation
- [x] Memory compactor — nightly consolidation of episode memory
- [x] Core memory — persistent identity/personality context loaded on every request
- [x] `manage_knowledge_base` — agent-facing read/write access to long-term intelligence store

### Skills — Web
- [x] `web_search` — DuckDuckGo search with result formatting
- [x] `deep_research` — multi-source research pipeline
- [x] `control_browser` — full Playwright automation: navigate, click, type, select, check, screenshot, extract text
- [x] Multi-browser support — engine selection for AI web automation (Chromium, Firefox, WebKit)

### Skills — Workspace & Dev
- [x] File operations — read, write, patch, delete (host and workspace variants)
- [x] Directory tools — scan, search in files, append, update
- [x] Git tools — status, diff, commit, checkout, branch, restore
- [x] `map_file_dependencies` — static dependency graph for a codebase
- [x] `run_python` — persistent sandboxed Python REPL
- [x] `run_shell_command` — direct host shell execution
- [x] `run_polyglot` — auto-detect language, compile (if needed), and execute source files
- [x] `dev_file` — single-file dev skill with syntax checking and auto-fix loop
- [x] `dev_files` — multi-file simultaneous write + execute for interdependent modules
- [x] `code_review` — diff-based architectural, performance, and stylistic code review
- [x] `feature_dev` — end-to-end feature development workflow (plan → implement → verify)

### Skills — System
- [x] `check_hardware_stats` — CPU, RAM, GPU, disk
- [x] `check_wade_services_health` — Ollama, ChromaDB, voice, monitors
- [x] `get_current_time` — timezone-aware time query with optional global location
- [x] `hot_reload_system` — live skill reload without restart
- [x] `perform_system_recovery` — system self-repair capabilities
- [x] `escalate_cognition` — dynamic reasoning model upgrade with hardware safety check

### Skills — Vision
- [x] `analyze_screen` — captures host monitor screenshot and analyzes it with Vision AI

### Skills — Data & Intelligence
- [x] `calculate_math` — complex math evaluation with numpy, sympy, scipy, and pandas
- [x] `get_weather` — real-time weather and daily forecast via IP-based geolocation
- [x] `get_global_recon_intel` — RSS-based global/country news intelligence with volatility index
- [x] `get_aero_flow_telemetry` — real-time ADS-B flight tracking and corporate jet pattern analysis

### Skills — Productivity
- [x] `notion` — full Notion CRUD: pages, databases, blocks, properties
- [x] `schedule_task` — one-time and recurring background job scheduler (natural language + cron)

### Skills — Media
- [x] `manage_spotify` — Windows Spotify control via media keys and Spotify Web API

### Skills — Security & Cameras
- [x] `get_home_security_status` — Blink camera system: status, arm/disarm, live snapshot

### Skills — Indexing & Knowledge Base
- [x] `get_knowledge_inventory` — summary of all files and folders indexed in the knowledge base
- [x] `search_indexed_files` — semantic search across indexed files with cross-encoder reranking
- [x] `reset_database` — permanent wipe of ChromaDB vector memory and SQLite indexer state

### Skills — WhatsApp
- [x] `whatsapp_send_message` — send a text message to a phone number or contact JID
- [x] `whatsapp_create_group` — create a group chat with name-resolved participants
- [x] `whatsapp_lookup_contact` — search the synced contact list by name to resolve JIDs

### Proactive Monitoring
- [x] Filesystem monitor — watch paths for changes
- [x] Schedule monitor — time-based task triggers
- [x] System monitor — resource threshold alerts
- [x] Proactive monitor — background initiative engine

### Multi-User & Tier Isolation
- [x] `TierContext` isolation — admin, family, friends, guests, strangers
- [x] `UserRegistry` — resolves WhatsApp and device IDs to tiers
- [x] Admin panel UI — user management, history viewer, and device registration

### Critic Layer
- [x] Multi-stage validation — anchor structure, plan feasibility, step execution, terminal satisfaction
- [x] Two-threshold escalation — automatic escalation to reasoner models on low confidence
- [x] Robust JSON parsing — regex-based extraction to handle conversational model output

### Security
- [x] Endpoint authentication — all sensitive API routes gated behind `require_admin`
- [x] Sandbox hardening — RLIMIT, seccomp BPF blocklist (Linux), bubblewrap per-call isolation, Windows Job Objects
- [x] Asset integrity — SHA-256 verification for all downloaded ONNX binaries with atomic temp-file rename

### UI & Developer Experience
- [x] Credentials & Integrations tab — sidebar accordion UI to save, clear, and live-test API keys for OpenAI, Anthropic, Gemini, Notion, Blink, and Spotify; keys stored locally via `CredentialsManager`, never returned by the API
- [x] Smooth tab transitions — CSS visibility/pointer-events approach replaces display:none so opacity and transform transitions actually fire
- [x] Custom confirmation modals — `_wadeConfirm()` and `_wadeAlert()` replace all native browser `confirm()`/`alert()` calls with styled dark-theme modals
- [x] `CONTRIBUTING.md` — open-source contributing guide covering setup, project layout, skill authoring, PR conventions, and security policy

---

## Planned

### Near-term
- [ ] **Better Proactive AI** - Update the proactive side of W.A.D.E to be significantly more intelligent.
- [ ] **Mobile Companion App** - Native iOS/Android bridge for W.A.D.E. notifications and control.

### Mid-term
- [ ] **Voice improvements** — wake-word reliability and conversational awareness
- [ ] **Google integrations** — Gmail, Google Calendar, Google Drive
- [ ] **Linear integration** — issue tracking auth flow
- [ ] **Skill marketplace** — install community skills from a registry URL

### Long-term
- [ ] **Multi-agent coordination** — W.A.D.E spawning sub-agents for parallel subtasks
- [ ] **Persistent browser sessions** — maintain login state across requests
- [ ] **Home automation** — Home Assistant integration
- [ ] **Local model fine-tuning pipeline** — skill-specific LoRA adapters
