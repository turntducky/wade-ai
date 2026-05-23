# W.A.D.E. — Wireless Autonomous Digital Entity  
### The Local-First Autonomous Runtime. Own your intelligence.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-0.1.0--beta-green.svg)](https://github.com/turntducky/wade-ai/releases)
[![GitHub Stars](https://img.shields.io/github/stars/turntducky/wade-ai?style=social)](https://github.com/turntducky/wade-ai/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/turntducky/wade-ai?style=social)](https://github.com/turntducky/wade-ai/network/members)

W.A.D.E. transforms your machine into an always-on, local autonomous runtime engineered to track context, monitor your environment, and process events proactively—aiming to keep your intelligence under your direct control.

It is not a passive interface or a chatbot framework. It is designed as a **personal AI infrastructure layer** that runs continuously as a background daemon within your operating system.

**W.A.D.E. is not prompt-bound. It is always running.**

---

## ⚡ W.A.D.E. in Motion: An Illustrative Execution Trace
To understand W.A.D.E., you have to look past the standard prompt box. Here is a representative scenario of how the architecture processes environmental changes asynchronously:

> You open your laptop. W.A.D.E. is running silently as a background service. Via its local event listener, it registers that you just pulled a broken upstream commit in your active repository. By ingesting the background error logs generated during compilation, it isolates the missing dependency, maps the structural break, and stages a local git patch. When you open the God Mode HUD, the system isn't waiting for a question—it is waiting for approval to apply the fix.

### The Real-World Loop:
```text
[OS Event: FS_WRITE] ──> You save a broken python file
[Event Bus]           ──> Direct ingestion of system telemetry into structured cognitive signals
[Planner Loop]        ──> Executor spins up sandboxed linting tool & catches exception
[Critic Verdict]      ──> Validates a 3-line patch via local model reasoning
[Trinity Memory]      ──> Logs the fix into Episodic Memory; updates your long-term Dev profile
```

---

## 🚫 What W.A.D.E. is NOT
To understand what W.A.D.E. is building, it helps to understand what it explicitly avoids:

- **NOT a Chatbot Wrapper:** It is not an alternate UI for sending prompt strings to cloud APIs.
- **NOT a Simple Orchestration Framework:** It is an asynchronous execution runtime, not a linear chain of hardcoded prompt scripts.
- **NOT a Cloud-Dependent Agent System:** The core engine is architected to operate entirely without internet access, keeping data localized to your hardware.
- **NOT an OS Replacement:** It sits on top of your existing operating system as an observational substrate, managing background workflows via standard system APIs.

---

## 👨‍💻 Founder Note
> I’m a 24-year-old solo dev building W.A.D.E. because I believe intelligence should be owned, not rented.  
> The architecture of modern AI forces an unacceptable compromise: trading privacy for utility. W.A.D.E. breaks that cycle by creating a system that lives entirely on local hardware, adapts to its user over time, and never requires surrendering personal data to external infrastructure. This is a commitment to absolute digital autonomy, privacy, and building a foundation for personal intelligence that outlives ephemeral corporate platforms.    
> On a personal note, as a soon-to-be father, this project has a deeper timeline. I want my daughter to grow up in a world where her digital assistant belongs entirely to her—not a corporation harvesting her data. W.A.D.E. is my bet on a future where privacy and autonomy are human rights, not product features. Every line of code is written to ensure that the user, not the provider, is in control.
> — *turnt ducky*

---

## 🏗️ Why W.A.D.E.?
- **Local Sovereignty:** Engineered for zero data leakage. Every byte—chat logs, personal facts, and vector embeddings—is kept on your hardware. W.A.D.E. interfaces natively with Ollama, eliminating mandatory subscription dependencies.
- **System Observability:** Instead of waiting for manual user input, W.A.D.E. maps OS-level events (`FS_CHANGE`, `SYS_THRESHOLD`) directly into its context layer via the **OS-to-Cognition Event Bus**, translating raw system telemetry into live cognitive signals.
- **Escalated Cognitive Scaling:** Local-first execution with optional, configurable escalation to frontier models **(GPT-4o, Claude 3.5, or Gemini)** via namespace tags when reasoning depth exceeds local hardware capacity.
- **Extensible Tool Construction:** Implement capabilities using the `@wade_tool` SDK. Build type-safe, sandboxed Python skills that hot-reload without forcing a runtime restart.

---

## 🛡️ Deterministic Guardrails & Safety
Autonomy requires trust. W.A.D.E. operates under strict behavioral boundaries to ensure system stability:

- **Configurable Permissions Thresholds:** No destructive autonomous actions (e.g., git commits, shell executions, filesystem deletions) are performed without explicit, user-defined permission gates.
- ***Sandboxed Execution Environments:** Tools run inside isolated sub-processes, restricting access to designated directories and verified local APIs.
- **Manual Overrides:** The daemon can be restricted to low-impact "Observation Mode" at any time, silencing execution capabilities while maintaining memory continuity.

---

## ⚡ God Mode: Live Cognitive Visibility
*Observability is the bridge between a black box and a trusted system.*

W.A.D.E. exposes its internal reasoning process as a live graph via the God Mode HUD. Think of it as a low-overhead debugger for cognition itself—allowing you to inspect how plans form, mutate, and execute.

**The God Mode HUD displays:**
- Live task graph evolution (nodes forming, splitting, and converging as OS events fire)
- Planner -> Executor -> Critic decision flow
- Live memory transactions and vector repository injections as they occur
- Isolated tool execution logs and sandbox diagnostics
- Probability distribution and confidence shifts across active reasoning paths

---

## 🧠 Core Systems
### 1. OS-to-Cognition Event Bus (Core Moat Layer)
W.A.D.E. utilizes a three-tier memory architecture to establish continuous context across long execution windows:

- **Short-Term Memory:** Sliding-window working logs featuring dynamic token trimming for active task execution threads.
- **Episodic Memory:** A temporal SQLite database archiving local system events, history, facts, and tool execution logs.
- **Semantic Memory:** A ChromaDB-backed long-term knowledge graph gated by strict relevance-score filtering for targeted vector retrieval.

### 2. Multi-Agent Execution Pipeline (Reasoning & Safety)
Every inferred target is processed through a coordinated validation loop:

- **Planner:** Translates complex top-level requests into dependency-ordered task graphs, grounded via Context Fusion from live system signals.
- **Executor:** An isolated engine that runs platform commands and tool workflows safely, featuring built-in recursion tracking and loop detection.
- **Critic:** A stateful validator evaluating tool performance against safety constraints, capable of halting execution or escalating tasks to higher-parameter models when confidence thresholds break.

### 3. OS-to-Cognition Event Bus (The Observational Layer)
The primary architectural moat. It handles the low-level interception and conversion of operating system and hardware vitals into structured, semantic signals for the memory and execution pipelines. It is the system's underlying sensory mechanism, tracking events like filesystem mutations and background logs without interrupting active user workflows.

---

## 🔁 Architecture Overview
```text
OS + User + System Signals   (FS_CHANGE, SYS_THRESHOLD)
         ↓
OS-to-Cognition Event Bus 
(System Signal Interpreter) 
         ↓
Cognitive Execution Loop  
(Planner → Executor → Critic)  
         ↓
Trinity Memory Layer 
(Short-Term | Episodic | Semantic)   
         ↓
Tool / System Actions  
         ↓
Continuous Feedback Loop
```

Detailed system architecture:
```text
          ┌──────────────────────┐
          │   Operating System   │
          └─────────┬────────────┘
                    │
                    ▼
     ┌──────────────────────────────┐
     │ OS-to-Cognition Event Bus    │
     │ (System Signal Interpreter)  │
     └──────────────┬───────────────┘
                    │
                    ▼
     ┌──────────────────────────────┐
     │ Cognitive Execution Loop     │
     │ Planner → Executor → Critic  │
     └──────────────┬───────────────┘
                    │
                    ▼
     ┌──────────────────────────────┐
     │       Trinity Memory         │
     └──────────────┬───────────────┘
                    │
                    ▼
     ┌──────────────────────────────┐
     │     Skills / Tool Layer      │
     └──────────────────────────────┘
```

---

## 🚀 Quick Start
Within seconds, your machine becomes a reactive cognitive system.

### Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com/) (Recommended for Local-Only)
- FFmpeg (Required for localized voice processing)

### Install & Launch
```bash
# Install the cognitive runtime
pip install "wade-ai[all]"

# Interactive setup (Hardware Scan + Cognition Source selection)
wade setup

# Boot the entity
wade start

# Open the dashboard
wade ui
```

###📦 What Happens Post-Installation?
Once started, W.A.D.E. initializes as a background loop daemon (`waded`) and registers the following resources locally:

- **Local API Server:** Hosted at `localhost:8085` to interface with your system shell and IDE plugins.
- **Web UI Dashboard:** Accessible via `wade ui` to monitor metrics, modify configuration tables, and adjust agent constraints.
- **Active Event Listener:** Attaches low-level hooks to file changes and process changes in designated work paths.
- **Isolated Skill Runtime:** Mounts a secure runtime directory for loading custom code modules.

---

## 🛠️ The Skill Layer & Tool Ecosystem
W.A.D.E. is an extensible substrate. Skills are modular cognitive capabilities that plug directly into the core runtime. If you can define a capability, you can compile it into a Skill, allowing W.A.D.E. to adapt to any workflow instantly.

### Writing a Custom Skill
Developers can extend W.A.D.E.'s execution layer instantly using the type-safe Python SDK. Tools hot-reload into the system daemon without requiring an environment restart:

```python
from wade.sdk import wade_tool
from wade.core.context import WorkspaceContext

@wade_tool(
    name="analyze_workspace_health",
    description="Audits local repo status when a file system change breaks tests."
)
async def analyze_workspace_health(ctx: WorkspaceContext, repo_path: str) -> dict:
    # Skills hook directly into the low-latency OS-to-Cognition Event Bus
    vitals = await ctx.system.get_git_status(repo_path)
    if vitals.has_untracked_failures:
        return {"status": "degraded", "suggested_patch": vitals.last_diff}
    return {"status": "nominal"}
```

### Out-of-the-Box Capabilities (60+ Built-in Tools)
W.A.D.E. ships pre-configured with a comprehensive suite of native tools to immediately manipulate and interface with your digital environment:

| Category | Modules & Capabilities |
| :--- | :--- |
| 💻 **Workspace** | Git (Commit/Diff tracking), Multi-File Patching, Dependency Tree Mapping |
| 🛠️ **Dev Utilities** | Automated Code Review, Sandboxed Python Execution, Log Analysis, Feature Dev Pipelines |
| 🌐 **Web Cognition** | Iterative Deep Research, Playwright Browser Control, Live News Intel |
| 🛰️ **Recon & Data** | Flight Telemetry Parsing, Real-Time Market Data, Vision-Based UI Analysis |
| 🏠 **Integrations** | Notion Workspace Sync, Spotify API, Blink Camera Feeds, WhatsApp (Group & Voice Management) |
| ⚙️ **System Control** | Hardware Vitals Auditing, Hot Reloading, Cognitive-Escalation Hooks |

---

## 🗺️ Roadmap
- [x] **Multi-Provider LLM Support** (OpenAI, Gemini, Claude)
- [x] **OS-to-Cognition Event Bus** (Context Fusion grounded planning)
- [x] **Trinity Memory System** (Short-Term, Episodic, and Semantic Tiers)
- [x] **God Mode** Observability HUD & System Graph
- [ ] **Near-term:** Proactive Cognition (Saliency-driven autonomous task creation)
- [ ] **Near-term:** Mobile Companion Runtime & App (iOS/Android)
- [ ] **Mid-term:** Local Workspace Sync (Native Email/Calendar for Thunderbird & Outlook)
- [ ] **Mid-term:** Expanded Skill Marketplace Ecosystem
- [ ] **Long-term:** Distributed Home Voice Nodes (Raspberry Pi hardware modules for local home audio)
- [ ] **Long-term:** Persistent Multi-Device Memory Fabric & Fully Autonomous Background Agents

---

## 🤝 Contributing
W.A.D.E. is a movement for local autonomy and open cognitive infrastructure. We welcome contributions ranging from core reasoning improvements to new modular features.

- **Build a Skill:** Tap into the type-safe `wade_tool` SDK to expand capabilities.
- **Refine the Core:** Optimize memory systems, safety guardrails, or event-bus latency. Check out our `tests/` suite.
- **Connect:** Join our community spaces to share ideas, updates, and custom configurations.

**Discussions**: [GitHub Issues + PRs](https://github.com/turntducky/wade-ai)  
**Updates**: [Follow W.A.D.E on X/Twitter](https://x.com/turntducky)

---

## ⚖️ License & Ecosystem
W.A.D.E. Core is permanently **MIT Licensed**. The core runtime will always remain entirely open source, local-first, and free for individual developers.

Future modular layers may include optional secure cloud sync infrastructure, distributed multi-device orchestration tools, and enterprise cognitive management systems. These commercial features will extend utility for production teams without ever constraining or locking down the core open-source engine.

---

## ⭐ Support Local Intelligence
If this project resonates with the idea of local-first, user-owned computing architecture, **consider starring this repository**. It helps signal to the open-source community that personal AI infrastructure should remain decentralized, open, and fully within individual control.

---

<p align="center"> Built as infrastructure for personal intelligence; not software, but a new computing layer. </p>
<p align="center"> Developed by <b> turnt ducky </b>.</p>