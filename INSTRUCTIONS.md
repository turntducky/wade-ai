# W.A.D.E. — Setup & Usage Instructions

This guide covers everything you need to get W.A.D.E. running from scratch, configure optional features, and extend it with custom skills.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [First-Time Setup](#3-first-time-setup)
4. [Starting & Stopping](#4-starting--stopping)
5. [The Web UI](#5-the-web-ui)
6. [CLI Reference](#6-cli-reference)
7. [Configuration](#7-configuration)
8. [Optional: Voice Interface](#8-optional-voice-interface)
9. [Optional: WhatsApp Bridge](#9-optional-whatsapp-bridge)
10. [Optional: Cloud LLM Providers](#10-optional-cloud-llm-providers)
11. [Custom Skills](#11-custom-skills)
12. [Docker Deployment](#12-docker-deployment)
13. [Directory Structure](#13-directory-structure)
14. [Troubleshooting](#14-troubleshooting)
15. [Uninstalling](#15-uninstalling)

---

## 1. Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | [python.org](https://www.python.org/downloads/) |
| pip | latest | `python -m pip install --upgrade pip` |
| [Ollama](https://ollama.com/) | latest | Required for local LLM inference |
| FFmpeg | any | Required for voice — [ffmpeg.org](https://ffmpeg.org/download.html) |
| Node.js | 18+ | Required only for the WhatsApp bridge |

> **Windows users:** Run your terminal as Administrator for the first `wade start` if you haven't installed God Mode (see [Section 6](#6-cli-reference)).

---

## 2. Installation

```bash
# Core runtime + all optional extras (recommended)
pip install "wade-ai[all]"

# Or install only what you need:
pip install "wade-ai"           # Core only (no local LLM, no voice, no browser)
pip install "wade-ai[llm]"     # + Ollama / local model support
pip install "wade-ai[voice]"   # + Wake word, STT, TTS
pip install "wade-ai[web]"     # + Playwright browser automation
```

After installation, the `wade` command is available globally.

---

## 3. First-Time Setup

Run the interactive setup wizard. It scans your hardware, selects appropriate models, and writes your config:

```bash
wade setup
```

The wizard will ask you to choose a **Cognition Source**:

| Mode | Description | Best For |
|------|-------------|----------|
| **Local** | All inference via Ollama on your machine | Maximum privacy, no internet required |
| **Hybrid** | Local for routine tasks, cloud for deep reasoning | Balanced performance |
| **Cloud** | All inference via OpenAI / Gemini / Claude | Fastest responses, requires API keys |

Once setup completes, pull your configured models:

```bash
wade fit
```

This downloads any Ollama models selected during setup. Large models may take several minutes.

---

## 4. Starting & Stopping

```bash
wade start      # Start the background daemon
wade stop       # Stop the daemon
wade restart    # Restart the daemon
wade status     # Show running status, URL, active model
```

**Windows — Silent Startup (God Mode):**

To avoid UAC prompts on every start/stop, register W.A.D.E. as a Windows Scheduled Task once:

```bash
wade godmode    # Run once as Administrator
```

After this, `wade start` and `wade stop` run silently without elevation prompts.

---

## 5. The Web UI

Once the daemon is running, open the dashboard:

```bash
wade ui
```

Or navigate directly to `http://localhost:8000/ui` in any browser.

**What's in the UI:**

- **Chat** — Talk to W.A.D.E. directly; runs the full planner → executor → critic pipeline
- **God Mode HUD** — Live task graph, tool execution traces, memory transactions
- **Settings** — Model routing, tier permissions, monitor configuration
- **Memory** — Browse and manage episodic and semantic memory
- **Workspace** — View and edit files in the active workspace

---

## 6. CLI Reference

| Command | Description |
|---------|-------------|
| `wade setup` | Run the interactive setup wizard |
| `wade start` | Start the background daemon |
| `wade stop` | Stop the daemon |
| `wade restart` | Restart the daemon |
| `wade status` | Show running status and active configuration |
| `wade ui` | Open the web dashboard in a browser |
| `wade fit` | Download all Ollama models from your config |
| `wade config` | Adjust LLM provider, model, or API keys |
| `wade logs` | View the daemon log (`-n 50` for last 50 lines) |
| `wade logs --follow` | Stream log output in real time |
| `wade pair` | WhatsApp pairing mode — shows QR code to scan |
| `wade talk` | Activate voice interface (wake word: "Wade") |
| `wade godmode` | Register silent Windows scheduled tasks (run as Admin once) |
| `wade version` | Print version, Python version, and active model |
| `wade uninstall` | Remove all W.A.D.E. data and optionally the package |

---

## 7. Configuration

Your configuration lives at `~/.wade/config.yaml`. You can edit it directly or use `wade config`:

```bash
# Switch LLM provider
wade config --provider ollama
wade config --provider openai
wade config --provider gemini
wade config --provider anthropic

# Set a specific model for a provider
wade config --provider ollama --model llama3.2:3b
```

**Key config fields:**

```yaml
port: 8000                    # Web UI and API port
llm:
  provider: ollama            # ollama | openai | gemini | anthropic
  ollama:
    model: llama3.2:3b
models:
  fast: llama3.2:3b           # Used for quick tasks and tool calls
  tools: llama3.2:3b          # Used for tool-heavy reasoning
  reasoner: llama3.3:70b      # Used for complex planning (escalation)
```

Changes take effect immediately — the daemon reloads config on each request.

---

## 8. Optional: Voice Interface

Voice requires additional system libraries:

**macOS:**
```bash
brew install portaudio ffmpeg
pip install "wade-ai[voice]"
```

**Linux:**
```bash
sudo apt-get install portaudio19-dev ffmpeg
pip install "wade-ai[voice]"
```

**Windows:**
```bash
# Install ffmpeg and add it to PATH, then:
pip install "wade-ai[voice]"
```

On first use, W.A.D.E. automatically downloads:
- The custom "Wade" wake word model
- Kokoro ONNX text-to-speech models
- Whisper speech-to-text (base or small, depending on your VRAM)

**Activate voice mode:**
```bash
wade talk
```

Say **"Wade"** to activate listening. W.A.D.E. transcribes your speech, processes the request, and speaks the response back.

---

## 9. Optional: WhatsApp Bridge

The WhatsApp bridge lets W.A.D.E. receive and send messages via WhatsApp Web. It runs as a separate Node.js process.

**Requirements:** Node.js 18+

**Step 1 — Pair your phone:**

```bash
wade pair
```

A QR code appears in the terminal. Scan it with **WhatsApp → Linked Devices → Link a Device**. Your session is saved to `~/.wade/wa_session/` and persists across restarts.

**Step 2 — Start the bridge alongside the daemon:**

The bridge runs automatically when you use `docker compose up` (see [Section 12](#12-docker-deployment)), or you can run it manually:

```bash
cd deploy/docker
node whatsapp-bridge.js
```

**How it works:**

- Direct messages to the linked number are forwarded to W.A.D.E. as chat requests
- Voice notes are transcribed and processed
- Group messages trigger a response only when "Wade" is mentioned or the bot is @-tagged
- W.A.D.E. can send messages and voice notes back using the `whatsapp_send_message` skill

---

## 10. Optional: Cloud LLM Providers

W.A.D.E. supports OpenAI, Google Gemini, and Anthropic Claude as drop-in replacements or escalation targets for Ollama.

**Save API keys:**
```bash
wade config --openai-key sk-...
wade config --gemini-key AIza...
wade config --anthropic-key sk-ant-...
```

Keys are stored encrypted in `~/.wade/` and never leave your machine.

**Use a cloud model:**
```bash
wade config --provider openai --model gpt-4o
wade config --provider gemini --model gemini-2.0-flash
wade config --provider anthropic --model claude-opus-4-7
```

**Per-request routing:** You can also address any model directly in a chat by prefixing with `provider/model`:

```
openai/gpt-4o: Summarise this document...
anthropic/claude-opus-4-7: Review this architecture...
```

---

## 11. Custom Skills

W.A.D.E. hot-reloads skills from `~/.wade/skills/` without restarting.

**Create a skill:**

```python
# ~/.wade/skills/my_skill.py
from app.skills.sdk import wade_tool

@wade_tool(
    name="my_skill",
    description="Does something useful.",
    category="custom",
    risk="low",
    parameters={
        "input": {"type": "string", "description": "What to process."}
    },
    required_params=["input"],
)
async def my_skill(input: str) -> str:
    return f"Processed: {input}"
```

**Reload without restart:**

```bash
# Ask W.A.D.E. in the chat UI:
"Hot reload the system"

# Or via CLI (restarts the daemon):
wade restart
```

> **Security note:** Skills in `~/.wade/skills/` run in the main process with full host privileges. Only install skills you trust.

**Sidecar `.md` files** (optional): Place a `my_skill.md` alongside your `.py` to add YAML frontmatter for richer metadata — category, risk level, per-tier access control, and extended instructions the LLM sees when routing to your skill.

---

## 12. Docker Deployment

Docker is the recommended way to run the **WhatsApp bridge** alongside the gateway on a server or always-on machine.

**Requirements:** Docker + Docker Compose V2

```bash
cd deploy/docker

# First run: creates ~/.wade directories and builds images
bash docker-setup.sh

# Subsequent starts
docker compose up -d

# View logs
docker compose logs -f

# Stop everything
docker compose down
```

**Services:**

| Service | Container | Description |
|---------|-----------|-------------|
| `gateway` | `wade_gateway` | The core Python runtime, serves the web UI on port 8000 |
| `whatsapp-bridge` | `wade_whatsapp_bridge` | Node.js Baileys bridge — connects WhatsApp Web to the gateway |

**Volumes:**

| Host Path | Description |
|-----------|-------------|
| `~/.wade/` | All persistent data: config, memory, workspace, models |
| `~/.wade/wa_session/` | WhatsApp session credentials — persists across container restarts |

**Environment variables** (set in `.env` at repo root):

```env
# Required for cloud LLM providers
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIza...
ANTHROPIC_API_KEY=sk-ant-...

# Optional
OLLAMA_HOST=http://host.docker.internal:11434
```

---

## 13. Directory Structure

All W.A.D.E. data lives under `~/.wade/`:

```
~/.wade/
├── config.yaml          # Main configuration
├── gateway.log          # Daemon log file
├── gateway.pid          # Daemon process ID
├── tasks.db             # Task tracking database
├── models.lock          # Model download state
├── data/
│   └── voices/          # Downloaded TTS/STT models (Kokoro, Whisper, wake word)
├── memory/
│   └── episodes.db      # Episodic (conversation) memory
├── workspace/           # Active file workspace
├── skills/              # Custom user skills (hot-reloaded)
│   ├── session/         # Session-scoped skills
│   └── pending/         # Skills staged for next load
├── monitors/            # Custom monitor configurations
└── wa_session/          # WhatsApp session credentials
```

---

## 14. Troubleshooting

**W.A.D.E. won't start on Windows**

Run your terminal as Administrator, or set up God Mode first:
```bash
# In an Administrator terminal, run once:
wade godmode
# Then start normally from any terminal:
wade start
```

**"Ollama is not reachable"**

Make sure Ollama is running before starting W.A.D.E.:
```bash
ollama serve          # Start Ollama
wade start            # Then start W.A.D.E.
```

**"Model not found"**

Run `wade fit` to download any missing models, or pull them directly:
```bash
ollama pull llama3.2:3b
```

**Voice won't initialize**

- Ensure FFmpeg is installed and on your PATH: `ffmpeg -version`
- Ensure a microphone is connected and accessible
- Check `wade logs` for specific error messages from the voice service

**WhatsApp session expired**

Re-run pairing mode:
```bash
wade pair
```
Scan the QR code again. The new session overwrites `~/.wade/wa_session/`.

**Skills not appearing after adding to `~/.wade/skills/`**

Ask W.A.D.E. in the chat: `"Hot reload the system"`, or run `wade restart`.

**Check the logs for anything else:**
```bash
wade logs -n 100
wade logs --follow   # Stream in real time
```

---

## 15. Uninstalling

```bash
wade uninstall
```

This interactively removes all data directories, the PID file, scheduled tasks (Windows), and optionally the `wade-ai` pip package.

To remove just the package without touching your data:
```bash
pip uninstall wade-ai
```
