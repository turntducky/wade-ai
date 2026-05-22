# Contributing to W.A.D.E.

W.A.D.E. is a local-first autonomous runtime built around the idea that your intelligence belongs to you. Contributions that sharpen the core engine, extend the skill layer, or improve safety and reliability are welcome.

---

## Before You Start

- Read the [README](README.md) to understand the project vision and architecture.
- Check [open issues](https://github.com/turntducky/wade-ai/issues) before starting work — someone may already be on it.
- For significant changes (new subsystems, architectural modifications), open an issue first to discuss the approach.

---

## Development Setup

```bash
# Clone the repo
git clone https://github.com/turntducky/wade-ai.git
cd wade-ai

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install with all optional dependencies
pip install -e ".[all]"

# Run the test suite
python -m pytest tests/ -q
```

**Prerequisites:**
- Python 3.10+
- [Ollama](https://ollama.com/) running locally (for inference-dependent tests)
- FFmpeg (for voice-related tests)

---

## Project Layout

```
app/
  agents/        # Planner, Executor, Critic agents + monitors
  api/           # FastAPI routes (God Mode HUD, WhatsApp, etc.)
  core/          # Config, credentials, event bus, orchestrator
  memory/        # Trinity Memory: short-term, episodic, semantic
  services/      # LLM inference, model routing, Ollama management
  skills/        # All built-in tools — this is where most contributions land
tests/
  unit/          # Unit tests (run without external services)
  integration/   # Integration tests (require Ollama)
  evals/         # Agent pipeline eval harness
```

---

## Ways to Contribute

### Build a Skill

Skills are the most accessible entry point. A skill is a Python module in `app/skills/<category>/` that exposes one or more async functions decorated with `@wade_tool`.

```python
from wade.sdk import wade_tool

@wade_tool(
    name="my_skill",
    description="What this skill does, in one sentence."
)
async def my_skill(param: str) -> dict:
    ...
    return {"result": ...}
```

After adding your skill, register it in `app/skills/registry.py` and add a corresponding entry in `app/skills/semantic_router.py` so the planner can route to it.

### Improve the Core

Core contributions include:
- **Memory systems** (`app/memory/`) — compaction, episodic retrieval, semantic search quality
- **Inference pipeline** (`app/services/inference_client.py`, `model_router.py`) — provider support, latency, error handling
- **Event bus** (`app/core/events.py`, `app/agents/monitors/`) — new OS-level signals, monitor reliability
- **Safety guardrails** (`app/core/security.py`) — permission gates, sandboxing

### Fix a Bug

Bug fixes are always welcome. If you're fixing something non-obvious, add a regression test. Run the full suite before opening a PR:

```bash
python -m pytest tests/ -q
```

All tests must pass (currently 373 passing, 2 skipped).

---

## Pull Request Guidelines

1. **One concern per PR.** Bug fix, feature, or refactor — not all three.
2. **Tests required for new behavior.** Add unit tests in `tests/unit/` for any new code paths.
3. **No secrets.** Never commit API keys, passwords, or personal credentials. Use `CredentialsManager` for runtime credential storage.
4. **Match the existing code style.** No formatter is enforced yet — just match the surrounding file.
5. **Keep PRs reviewable.** If a change touches more than ~10 files, split it or explain why it can't be.

### PR Title Format

```
feat: add spotify now-playing skill
fix: resolve chromadb embedding function deprecation
refactor: simplify model router resolve path
docs: update quick start for Windows users
```

---

## Security

If you find a security vulnerability, **do not open a public issue**. Email me directly or use GitHub's private vulnerability reporting. See [SECURITY.md](SECURITY.md) if present, or contact via the Discord server.

---

## Community

=- **GitHub Issues**: Bug reports, feature requests, architecture questions
- **X/Twitter**: [@wade_ai](https://x.com/wade_ai) — project updates

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE) that covers this project.