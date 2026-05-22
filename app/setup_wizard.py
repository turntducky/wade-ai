from __future__ import annotations

import sys
import shutil
import asyncio
import textwrap
import platform

from typing import Dict, Any, Optional, cast

def _run_async_safely(coro):
    """Run a coroutine safely regardless of whether an event loop is already running."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)

from app.core.config import ConfigManager, DUCK_HOME, CONFIG_FILE

def _check_ollama_installed() -> bool:
    """Returns True if the 'ollama' binary is on PATH."""
    return shutil.which("ollama") is not None

def _wait_for_ollama_install():
    """Blocks until the user confirms Ollama is installed."""
    print("\n[wade] Ollama is not installed.")
    print("   Download it from: https://ollama.com/download")
    print("   Install it, then press Enter to continue...")
    while True:
        input()
        if _check_ollama_installed():
            print("[wade] Ollama detected.")
            return
        print("   Still not found. Make sure Ollama is on your PATH, then press Enter...")

async def _run_preflight() -> bool:
    from app.services.ollama_manager import ollama_manager
    if not await ollama_manager.is_running():
        print("[wade] Preflight: Ollama is not running.")
        return False
    model = ConfigManager.get().get("roles", {}).get("mapping", {}).get("chat", "")
    if model and not await ollama_manager.model_exists(model):
        print(f"[wade] Preflight: Model '{model}' not found.")
        return False
    print("[wade] Preflight check passed -- W.A.D.E. is ready.")
    return True

class UI:
    """Zero-dependency ANSI terminal styling and layout helpers."""
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    @staticmethod
    def width() -> int:
        return min(shutil.get_terminal_size().columns, 75)

    @classmethod
    def header(cls, title: str, subtitle: str = "") -> None:
        w = cls.width()
        print(f"\n{cls.CYAN}╭{'─' * (w - 2)}╮{cls.RESET}")
        print(f"{cls.CYAN}│{cls.RESET} {cls.BOLD}{title.center(w - 4)}{cls.RESET} {cls.CYAN}│{cls.RESET}")
        if subtitle:
            print(f"{cls.CYAN}│{cls.RESET} {cls.DIM}{subtitle.center(w - 4)}{cls.RESET} {cls.CYAN}│{cls.RESET}")
        print(f"{cls.CYAN}╰{'─' * (w - 2)}╯{cls.RESET}\n")

    @classmethod
    def step(cls, n: int, total: int, title: str) -> None:
        print(f"\n{cls.DIM}{'─' * cls.width()}{cls.RESET}")
        print(f" {cls.CYAN}{cls.BOLD}Step {n}/{total}{cls.RESET} ⬢ {cls.BOLD}{title}{cls.RESET}")
        print(f"{cls.DIM}{'─' * cls.width()}{cls.RESET}\n")

    @classmethod
    def ok(cls, msg: str) -> None: 
        print(f"  {cls.GREEN}✔{cls.RESET}  {msg}")
        
    @classmethod
    def info(cls, msg: str) -> None: 
        print(f"  {cls.CYAN}●{cls.RESET}  {msg}")
        
    @classmethod
    def warn(cls, msg: str) -> None: 
        print(f"  {cls.YELLOW}⚠{cls.RESET}  {msg}")
        
    @classmethod
    def error(cls, msg: str) -> None: 
        print(f"  {cls.RED}✖{cls.RESET}  {msg}")

    @classmethod
    def ask(cls, prompt: str, default: str = "") -> str:
        hint = f" {cls.DIM}[{default}]{cls.RESET}" if default else ""
        try:
            answer = input(f"\n  {cls.BOLD}{prompt}{cls.RESET}{hint} ❯ ").strip()
            return answer if answer else default
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {cls.RED}Setup cancelled by user.{cls.RESET}")
            sys.exit(0)

    @classmethod
    def confirm(cls, prompt: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        answer = cls.ask(f"{prompt} {cls.DIM}({hint}){cls.RESET}", "y" if default else "n")
        return answer.lower() in ("y", "yes", "true", "")

def _detect_hardware() -> Dict[str, Any]:
    """Scans system resources to determine the optimal execution profile."""
    from app.core.hardware import probe_hardware
    from app.services.discovery import select_profile
    specs = probe_hardware()
    primary = specs.get("primary", {})

    info: Dict[str, Any] = {
        "cpu":        platform.processor() or platform.machine(),
        "ram_gb":     0.0,
        "gpu":        None,
        "vram_gb":    0.0,
        "usable_gb":  primary.get("memory_usable_gb", 0.0),
        "cuda":       False,
        "profile":    "tiny",
    }

    try:
        import psutil
        info["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        pass

    backend = primary.get("backend", "cpu").lower()
    if backend in ("cuda", "rocm", "metal"):
        info["cuda"]   = True
        info["vram_gb"] = round(primary.get("memory_total_gb", 0.0), 1)
        for dev in specs.get("devices", []):
            if dev.get("kind") == "gpu":
                info["gpu"] = dev.get("name")
                break

    info["profile"] = select_profile(info["usable_gb"])
    return info

def _profile_label(profile: str) -> str:
    labels = {
        "xl":     "XL         (40GB+ VRAM — 70B-class models)",
        "large":  "Large      (20GB+ VRAM — 32B-class models)",
        "medium": "Medium     (6GB+ VRAM — 7-8B models, best balance)",
        "small":  "Small      (3GB+ VRAM — 3B models, efficient)",
        "tiny":   "Tiny       (<3GB — 1.5B models, maximum compatibility)",
    }
    return labels.get(profile, profile)

_MODEL_RECOMMENDATIONS: Dict[str, Dict[str, str]] = {
    "xl": {
        "llm":  "qwen2.5:72b",
        "stt":  "whisper-large-v3-turbo",
        "desc": "Elite tier — 70B class reasoning with full multimodal vision support.",
    },
    "large": {
        "llm":  "qwen2.5:32b",
        "stt":  "whisper-large-v3-turbo",
        "desc": "High-end — 32B class models for exceptional coding and reasoning.",
    },
    "medium": {
        "llm":  "llama3.1:8b",
        "stt":  "whisper-base",
        "desc": "Sweet spot — fast 7-8B inference with strong general capabilities.",
    },
    "small": {
        "llm":  "llama3.2:3b",
        "stt":  "whisper-base",
        "desc": "Efficient — lightweight 3B models, low VRAM footprint.",
    },
    "tiny": {
        "llm":  "llama3.2:1b",
        "stt":  "whisper-tiny",
        "desc": "Minimal — 1-1.5B models for highly constrained environments.",
    },
}

def _build_config(hw: Dict[str, Any], chat_model: str, stt_model: str,
                  enable_voice: bool, enable_web: bool, port: int,
                  preferred_browser: str | None = None,
                  automation_browser: str = "chromium") -> Dict[str, Any]:
    from app.services.discovery import get_suite
    suite = get_suite(hw["profile"])
    suite = {**suite, "chat": chat_model}

    from app.services.model_manager import _suite_to_routing_table
    routing = _suite_to_routing_table(suite)

    cfg = {
        "models": routing,
        "active_suite": suite,
        "model_family": _infer_model_family({"chat": chat_model}),
        "memory": {
            "max_history": 20,
            "semantic":    True,
        },
        "voice": {
            "enabled":   enable_voice,
            "stt_model": stt_model,
            "wake_word": "wade",
        },
        "web": {
            "browser_enabled": enable_web,
        },
        "server": {
            "host": "0.0.0.0",
            "port": port,
        },
        "hardware": {
            "profile":   hw["profile"],
            "cuda":      hw["cuda"],
            "vram_gb":   hw["vram_gb"],
            "ram_gb":    hw["ram_gb"],
            "usable_gb": hw["usable_gb"],
        },
    }
    if preferred_browser:
        cfg["preferred_browser"] = preferred_browser
    if automation_browser != "chromium":
        cfg["automation_browser"] = automation_browser
    return cfg

def _infer_model_family(suite: dict) -> str:
    chat = str(suite.get("chat", "")).lower()
    for family in ("qwen", "llama", "mistral", "phi", "gemma", "deepseek"):
        if family in chat:
            return family
    return "default"

def _setup_blink_auth(email: str, password: str) -> None:
    """Attempt Blink login and handle 2FA interactively. Saves token on success."""
    from app.core.credentials import CredentialsManager

    try:
        from blinkpy.auth import Auth as BlinkAuth
        from blinkpy.blinkpy import Blink
    except ImportError:
        UI.warn("blinkpy not installed — credentials saved, but run 'pip install blinkpy' to use cameras.")
        return

    def _is_2fa(exc: Exception) -> bool:
        if type(exc).__name__ == "BlinkTwoFARequiredError":
            return True
        try:
            from blinkpy.helpers.errors import BlinkTwoFARequiredError  # type: ignore
            return isinstance(exc, BlinkTwoFARequiredError)
        except ImportError:
            return False

    async def _run() -> None:
        blink = Blink()
        blink.auth = BlinkAuth({"username": email, "password": password}, no_prompt=True)

        UI.info("Connecting to Blink...")
        try:
            await blink.start()
            creds = CredentialsManager.get("blink") or {}
            CredentialsManager.save("blink", {**creds, **blink.auth.login_attributes})
            UI.ok("Blink connected.")
            return
        except Exception as exc:
            if not _is_2fa(exc):
                UI.error(f"Blink login failed: {exc}")
                return

        UI.info("Blink texted a verification code to your registered phone number.")
        for attempt in range(3):
            pin = UI.ask("Verification code", default="").strip()
            if not pin:
                UI.warn("No code entered — skipping Blink setup. You can connect later in the Credentials tab.")
                return
            try:
                await blink.send_2fa_code(pin)
                creds = CredentialsManager.get("blink") or {}
                CredentialsManager.save("blink", {**creds, **blink.auth.login_attributes})
                UI.ok("Blink connected and token saved.")
                return
            except Exception as exc:
                if attempt < 2:
                    UI.error(f"Invalid code: {exc}  (attempt {attempt + 1}/3)")
                else:
                    UI.error("Too many failed attempts — skipping Blink setup. Try again from the Credentials tab.")

    _run_async_safely(_run())

def _setup_integrations() -> None:
    """Prompt for all optional third-party credentials. Each service is skippable."""
    from app.core.credentials import CredentialsManager

    print(f"\n  {UI.BOLD}Optional integrations{UI.RESET}  {UI.DIM}(press Enter to skip any){UI.RESET}\n")

    for provider, label, hint in [
        ("openai",    "OpenAI API Key",    "sk-..."),
        ("anthropic", "Anthropic API Key", "sk-ant-..."),
        ("gemini",    "Gemini API Key",    "AIza..."),
    ]:
        key = UI.ask(f"{label}  {UI.DIM}[{hint}]{UI.RESET}", default="")
        if key:
            CredentialsManager.save(provider, {"api_key": key})
            UI.ok(f"{label} saved.")

    print(f"\n  {UI.DIM}Notion: https://www.notion.so/my-integrations → New integration → copy token{UI.RESET}")
    notion_token = UI.ask("Notion Integration Token", default="")
    if notion_token:
        try:
            from notion_client import Client as _NC
            me = cast(dict, _NC(auth=notion_token).users.me())
            workspace = me.get("name") or me.get("bot", {}).get("workspace_name") or "workspace"
            CredentialsManager.save("notion", {"token": notion_token})
            UI.ok(f"Notion connected: {workspace}")
        except ImportError:
            CredentialsManager.save("notion", {"token": notion_token})
            UI.warn("notion-client not installed — token saved, but 'pip install notion-client' needed for live sync.")
        except Exception as e:
            UI.error(f"Notion token check failed: {e}")
            if UI.confirm("Save token anyway?", default=True):
                CredentialsManager.save("notion", {"token": notion_token})

    print(f"\n  {UI.DIM}Spotify: https://developer.spotify.com/dashboard → create app → copy Client ID & Secret{UI.RESET}")
    spotify_id = UI.ask("Spotify Client ID", default="")
    if spotify_id:
        spotify_secret = UI.ask("Spotify Client Secret", default="")
        if spotify_secret:
            CredentialsManager.save("spotify", {"client_id": spotify_id, "client_secret": spotify_secret})
            UI.ok("Spotify credentials saved.")

    if UI.confirm("\n  Set up Blink camera integration?", default=False):
        blink_email = UI.ask("Blink account email", default="")
        if blink_email:
            import getpass
            blink_pw = getpass.getpass("  Password ❯ ").strip()
            if blink_pw:
                CredentialsManager.save("blink", {"email": blink_email, "password": blink_pw})
                _setup_blink_auth(blink_email, blink_pw)

    UI.info("All integrations can be updated anytime via 'wade setup --advanced' or the Credentials tab.")

def _get_cloud_presets(provider: str) -> dict[str, str]:
    """Return standard cloud model presets."""
    if provider == "openai":
        return {
            "chat": "openai/gpt-4o-mini",
            "tools": "openai/gpt-4o",
            "planner": "openai/gpt-4o-mini",
            "reasoner": "openai/gpt-4o",
            "code": "openai/gpt-4o",
            "fast": "openai/gpt-4o-mini",
            "vision": "openai/gpt-4o",
            "embedding": "openai/text-embedding-3-small"
        }
    elif provider == "gemini":
        return {
            "chat": "gemini/gemini-1.5-flash",
            "tools": "gemini/gemini-1.5-pro",
            "planner": "gemini/gemini-1.5-flash",
            "reasoner": "gemini/gemini-1.5-pro",
            "code": "gemini/gemini-1.5-pro",
            "fast": "gemini/gemini-1.5-flash",
            "vision": "gemini/gemini-1.5-pro",
            "embedding": "gemini/text-embedding-004"
        }
    elif provider == "anthropic":
        return {
            "chat": "anthropic/claude-3-haiku-20240307",
            "tools": "anthropic/claude-3-5-sonnet-20241022",
            "planner": "anthropic/claude-3-haiku-20240307",
            "reasoner": "anthropic/claude-3-5-sonnet-20241022",
            "code": "anthropic/claude-3-5-sonnet-20241022",
            "fast": "anthropic/claude-3-haiku-20240307",
            "vision": "anthropic/claude-3-5-sonnet-20241022",
            "embedding": "openai/text-embedding-3-small"
        }
    return {}

def run_wizard(reinstall: bool = False, args=None, advanced: bool = False) -> None:
    """Fast single-prompt setup. Pass advanced=True for full configuration."""
    if advanced or getattr(args, "advanced", False):
        run_wizard_advanced(reinstall=reinstall, args=args)
        return

    if CONFIG_FILE.exists() and not reinstall:
        answer = input(f"[wade] Already configured. Re-run setup? [y/N] ").strip().lower()
        if answer != "y":
            print("[wade] Cancelled. Run 'wade start' to launch.")
            return

    if CONFIG_FILE.exists():
        backup = CONFIG_FILE.with_suffix(".yaml.bak")
        shutil.copy(CONFIG_FILE, backup)

    w = min(shutil.get_terminal_size().columns, 52)
    print(f"\n{UI.CYAN}{'─' * w}{UI.RESET}")
    print(f"  {UI.BOLD}W.A.D.E. Setup{UI.RESET}")
    print(f"{UI.CYAN}{'─' * w}{UI.RESET}\n")

    print(f"  {UI.DIM}Scanning hardware...{UI.RESET}", end="", flush=True)
    hw = _detect_hardware()
    print(f"  {UI.GREEN}done{UI.RESET}\n")

    user_name = UI.ask("What should I call you?", default="User")
    assistant_name = UI.ask("What should I name your assistant?", default="W.A.D.E.")

    print(f"\n  {UI.BOLD}Cognition Source{UI.RESET}")
    print(f"  {UI.CYAN}[1]{UI.RESET} {UI.BOLD}Local-Only{UI.RESET} (Ollama) — Recommended")
    print(f"  {UI.CYAN}[2]{UI.RESET} {UI.BOLD}Cloud-Only{UI.RESET} (OpenAI, Gemini, Anthropic)")
    cog_mode = UI.ask("Choice", default="1")
    
    llm_provider = "ollama"
    if cog_mode == "2":
        print("\n  Select provider:")
        print("  1) OpenAI  2) Gemini  3) Anthropic")
        p_choice = UI.ask("Choice", default="1")
        llm_provider = {"1": "openai", "2": "gemini", "3": "anthropic"}.get(p_choice, "openai")
        _setup_api_keys([llm_provider])

    from app.services.discovery import get_suite
    suite = get_suite(hw["profile"])
    if cog_mode == "2":
        suite = _get_cloud_presets(llm_provider)

    rec = _MODEL_RECOMMENDATIONS.get(hw["profile"], _MODEL_RECOMMENDATIONS["small"])

    def _row(label: str, value: str, dim_value: bool = False) -> None:
        v = f"{UI.DIM}{value}{UI.RESET}" if dim_value else f"{UI.CYAN}{value}{UI.RESET}"
        print(f"  {UI.BOLD}{label:<12}{UI.RESET} {v}")

    cap = rec["desc"].split("—", 1)[-1].strip()
    _row("Profile",   f"{hw['profile'].title()}  ·  {cap}")
    _row("User",      user_name)
    _row("Assistant", assistant_name)
    _row("Engine",    llm_provider.capitalize())
    _row("Chat",      suite["chat"])
    _row("Coding",    suite["coding"])
    _row("Reasoning", suite["reasoning"])
    _row("Embedding", suite["embedding"])
    _row("Vision",    suite.get("vision", "none"))
    _row("Fast",      suite.get("fast", "none"))
    _row("Port",      "8000", dim_value=True)
    print(f"\n  {UI.DIM}Stored in  {DUCK_HOME}{UI.RESET}")

    print(f"\n{UI.CYAN}{'─' * w}{UI.RESET}\n")
    print(f"  {UI.DIM}Target suite summary:{UI.RESET}")
    for role, model in suite.items():
        print(f"    {UI.DIM}{role:<12}{UI.RESET} → {UI.CYAN}{model}{UI.RESET}")
    
    print(f"\n  {UI.DIM}Press Enter to accept · type a model name to override chat · 'q' to quit{UI.RESET}")

    answer = UI.ask("", default=suite["chat"]).strip()
    if answer.lower() == "q":
        print("[wade] Setup cancelled.")
        return

    port = 8000
    cfg = _build_config(hw, answer, rec["stt"], False, False, port)
    cfg["user_name"] = user_name
    cfg["assistant_name"] = assistant_name
    cfg["llm"] = {"provider": llm_provider}

    print(f"\n  {UI.BOLD}Cognitive Indexer{UI.RESET}")
    print("  Choose which areas W.A.D.E. is allowed to scan.")
    
    zones = ["core"]
    if UI.confirm("  Index system documents? (OneDrive Documents/Desktop)", default=True):
        zones.append("system")
    if UI.confirm("  Index registered project directories?", default=True):
        zones.append("projects")
    
    custom_dirs = []
    if UI.confirm("  Add any custom directories now?", default=False):
        while True:
            p = UI.ask("  Path (or Enter to finish)").strip()
            if not p: break
            custom_dirs.append(p)

    cfg["indexer"] = {
        "enabled_zones": zones,
        "custom_dirs": custom_dirs
    }

    ConfigManager.save(cfg)

    print(f"\n  {UI.BOLD}Integrations{UI.RESET}  {UI.DIM}(all optional — press Enter to skip){UI.RESET}")
    _setup_integrations()

    print(f"\n  {UI.DIM}Initialising workspace...{UI.RESET}", end="", flush=True)
    from app.workspace import generate_cognitive_architecture
    generate_cognitive_architecture()
    print(f"  {UI.GREEN}done{UI.RESET}")

    print(f"\n  {UI.DIM}Pulling models — this may take a few minutes (Ctrl+C to defer){UI.RESET}\n")
    try:
        _run_async_safely(_download_models())
    except KeyboardInterrupt:
        UI.warn("Download interrupted. Run 'wade fit' to resume.")
    except Exception as e:
        UI.error(f"Download failed: {e}")
        UI.info("Run 'wade fit' later to retry.")

    print(f"\n{UI.CYAN}{'─' * w}{UI.RESET}")
    print(f"  {UI.GREEN}{UI.BOLD}Setup complete.{UI.RESET}")
    print(f"{UI.CYAN}{'─' * w}{UI.RESET}\n")
    print(f"  {UI.BOLD}wade start{UI.RESET}          Start the daemon")
    print(f"  {UI.BOLD}wade ui{UI.RESET}             Open the web interface")
    print(f"  {UI.BOLD}wade setup --advanced{UI.RESET}  Voice, web browsing, and more\n")

    _run_async_safely(_run_preflight())

    if UI.confirm("Start W.A.D.E. now?", default=True):
        print()
        from app.daemon import start_daemon
        start_daemon()

def _setup_api_keys(providers: list[str]) -> None:
    """Helper to collect and save API keys for selected providers."""
    from app.core.credentials import CredentialsManager
    for p in providers:
        UI.header(f"Configure {p.capitalize()}")
        key = UI.ask(f"Paste your {p.capitalize()} API Key", default="")
        if key:
            CredentialsManager.save(p, {"api_key": key})
            UI.ok(f"{p.capitalize()} key saved.")

def run_wizard_advanced(reinstall: bool = False, args=None) -> None:
    """Full interactive setup wizard with per-module configuration."""

    if not _check_ollama_installed():
        UI.warn("Ollama is not installed. Local-only mode will be unavailable.")

    if CONFIG_FILE.exists():
        print(f"\n[wade] W.A.D.E. is already configured at {CONFIG_FILE}")
        answer = input("   Re-run setup? This will back up your current config. [y/N] ").strip().lower()
        if answer != "y":
            print("Setup cancelled. Run 'wade start' to launch W.A.D.E.")
            return
        backup = CONFIG_FILE.with_suffix(".yaml.bak")
        shutil.copy(CONFIG_FILE, backup)
        print(f"   Config backed up to {backup}")

    UI.header("W.A.D.E. SETUP WIZARD", "Advanced")

    print(textwrap.dedent(f"""\
      Welcome! This wizard will configure W.A.D.E. for your hardware.
      All data and configurations are stored in: {UI.CYAN}~/.wade/{UI.RESET}
    """))

    user_name = UI.ask("What should I call you?", default="User")
    assistant_name = UI.ask("What should I name your assistant?", default="W.A.D.E.")

    UI.step(1, 6, "System Telemetry")
    hw = _detect_hardware()

    UI.info(f"Processor : {hw['cpu']}")
    UI.info(f"Memory    : {hw['ram_gb']} GB")

    if hw["cuda"]:
        UI.ok(f"Compute   : {hw['gpu']} ({hw['vram_gb']} GB VRAM) — CUDA Active")
    else:
        UI.warn("Compute   : No CUDA GPU detected. Defaulting to CPU execution.")

    rec = _MODEL_RECOMMENDATIONS.get(hw["profile"], _MODEL_RECOMMENDATIONS["small"])
    print(f"\n  {UI.DIM}Target Profile:{UI.RESET}  {UI.CYAN}{_profile_label(hw['profile'])}{UI.RESET}")
    print(f"  {UI.DIM}Capabilities:{UI.RESET}    {rec['desc']}")

    UI.step(2, 6, "Cognition Source")
    print(textwrap.dedent(f"""\
      W.A.D.E. is designed for {UI.BOLD}local-first{UI.RESET} operation for maximum privacy.
      However, you can also use Cloud APIs (OpenAI, Gemini, Anthropic).
    """))
    print(f"  {UI.CYAN}[1]{UI.RESET} {UI.BOLD}Local Only{UI.RESET} (Ollama) — {UI.GREEN}Recommended{UI.RESET}")
    print(f"  {UI.CYAN}[2]{UI.RESET} {UI.BOLD}API Hybrid{UI.RESET} — Local for daily chat, Cloud for heavy reasoning")
    print(f"  {UI.CYAN}[3]{UI.RESET} {UI.BOLD}Cloud Only{UI.RESET} — Fully offloaded to external APIs")

    cog_choice = UI.ask("Select an option", default="1")
    
    llm_provider = "ollama"
    cloud_providers = []
    
    if cog_choice == "2":
        print("\n  Select cloud providers to enable:")
        if UI.confirm("  Enable OpenAI?", default=True): cloud_providers.append("openai")
        if UI.confirm("  Enable Gemini?", default=True): cloud_providers.append("gemini")
        if UI.confirm("  Enable Anthropic?", default=True): cloud_providers.append("anthropic")
        _setup_api_keys(cloud_providers)
    elif cog_choice == "3":
        print("\n  Select primary cloud provider:")
        print("  1) OpenAI  2) Gemini  3) Anthropic")
        p_choice = UI.ask("Choice", default="1")
        llm_provider = {"1": "openai", "2": "gemini", "3": "anthropic"}.get(p_choice, "openai")
        _setup_api_keys([llm_provider])

    UI.step(3, 6, "Intelligence Core")
    from app.services.discovery import get_suite
    suite = get_suite(hw["profile"])

    if cog_choice == "2":
        if "anthropic" in cloud_providers:
            suite["reasoner"] = "anthropic/claude-3-5-sonnet-20241022"
        elif "openai" in cloud_providers:
            suite["reasoner"] = "openai/gpt-4o"
        elif "gemini" in cloud_providers:
            suite["reasoner"] = "gemini/gemini-1.5-pro"
        
        if "openai" in cloud_providers:
            suite["vision"] = "openai/gpt-4o"
    elif cog_choice == "3":
        if llm_provider == "openai":
            suite = {
                "chat": "openai/gpt-4o-mini",
                "tools": "openai/gpt-4o",
                "planner": "openai/gpt-4o-mini",
                "reasoner": "openai/gpt-4o",
                "code": "openai/gpt-4o",
                "fast": "openai/gpt-4o-mini",
                "vision": "openai/gpt-4o",
                "embedding": "openai/text-embedding-3-small"
            }
        elif llm_provider == "gemini":
            suite = {
                "chat": "gemini/gemini-1.5-flash",
                "tools": "gemini/gemini-1.5-pro",
                "planner": "gemini/gemini-1.5-flash",
                "reasoner": "gemini/gemini-1.5-pro",
                "code": "gemini/gemini-1.5-pro",
                "fast": "gemini/gemini-1.5-flash",
                "vision": "gemini/gemini-1.5-pro",
                "embedding": "gemini/text-embedding-004"
            }
        elif llm_provider == "anthropic":
            suite = {
                "chat": "anthropic/claude-3-haiku-20240307",
                "tools": "anthropic/claude-3-5-sonnet-20241022",
                "planner": "anthropic/claude-3-haiku-20240307",
                "reasoner": "anthropic/claude-3-5-sonnet-20241022",
                "code": "anthropic/claude-3-5-sonnet-20241022",
                "fast": "anthropic/claude-3-haiku-20240307",
                "vision": "anthropic/claude-3-5-sonnet-20241022",
                "embedding": "openai/text-embedding-3-small" # Anthropic doesn't have an embed API
            }

    print(f"  W.A.D.E. will use the following configuration:\n")
    print(f"    {UI.BOLD}Chat / General:{UI.RESET}  {UI.GREEN}{suite['chat']}{UI.RESET}")
    print(f"    {UI.BOLD}Coding:{UI.RESET}          {UI.GREEN}{suite['coding']}{UI.RESET}")
    print(f"    {UI.BOLD}Reasoning:{UI.RESET}       {UI.GREEN}{suite['reasoning']}{UI.RESET}")
    print(f"    {UI.BOLD}Embedding:{UI.RESET}       {UI.GREEN}{suite['embedding']}{UI.RESET}")
    print(f"    {UI.BOLD}Vision:{UI.RESET}          {UI.GREEN}{suite['vision']}{UI.RESET}")
    print(f"    {UI.BOLD}Voice STT:{UI.RESET}       {UI.GREEN}{rec['stt']}{UI.RESET}\n")

    print(f"  {UI.CYAN}[1]{UI.RESET} Use recommended {UI.DIM}(fastest setup){UI.RESET}")
    print(f"  {UI.CYAN}[2]{UI.RESET} Override models {UI.DIM}(manual model names){UI.RESET}")
    print(f"  {UI.CYAN}[3]{UI.RESET} Skip download {UI.DIM}(configure manually later){UI.RESET}")

    choice = UI.ask("Select an option", default="1")

    download_models = cog_choice in ("1", "2")
    custom_llm, custom_stt = suite["chat"], rec["stt"]

    if choice == "2":
        print(f"\n  {UI.DIM}Enter model names (e.g. qwen2.5:7b, openai/gpt-4o).{UI.RESET}")
        custom_llm = UI.ask("Chat / General model", default=suite["chat"])
        custom_stt = UI.ask("Speech-to-Text model", default=rec["stt"])
    elif choice == "3":
        download_models = False
        UI.info("Model download deferred. Run 'wade fit' when ready.")

    UI.step(4, 7, "Module Configuration")

    enable_voice = UI.confirm(
        "Enable Voice Interface? (wake word detection & spoken replies)",
        default=hw["cuda"],
    )
    enable_web = UI.confirm(
        "Enable Web Browsing Skill? (allows W.A.D.E. to navigate the web)",
        default=False,
    )

    automation_browser = "chromium"
    if enable_web:
        print(f"\n  {UI.BOLD}Browser engine for AI web automation?{UI.RESET}")
        print(f"  {UI.DIM}1) Chromium (recommended — required for remote browser service){UI.RESET}")
        print(f"  {UI.DIM}2) Firefox{UI.RESET}")
        print(f"  {UI.DIM}3) WebKit (Safari-compatible){UI.RESET}")
        engine_input = UI.ask("Choice", default="1")
        automation_browser = {"1": "chromium", "2": "firefox", "3": "webkit"}.get(engine_input, "chromium")

    print(f"\n  {UI.BOLD}Preferred browser for the W.A.D.E. web UI?{UI.RESET}")
    print(f"  {UI.DIM}1) System default (recommended){UI.RESET}")
    print(f"  {UI.DIM}2) Chrome{UI.RESET}")
    print(f"  {UI.DIM}3) Firefox{UI.RESET}")
    print(f"  {UI.DIM}4) Microsoft Edge{UI.RESET}")
    if sys.platform == "darwin":
        print(f"  {UI.DIM}5) Safari{UI.RESET}")
        print(f"  {UI.DIM}6) Opera{UI.RESET}")
        browser_map = {"1": None, "2": "chrome", "3": "firefox", "4": "edge", "5": "safari", "6": "opera"}
    else:
        print(f"  {UI.DIM}5) Opera{UI.RESET}")
        browser_map = {"1": None, "2": "chrome", "3": "firefox", "4": "edge", "5": "opera"}
    browser_input = UI.ask("Choice", default="1")
    preferred_browser = browser_map.get(browser_input)

    port_str = UI.ask("Web UI Port", default="8000")
    port = int(port_str) if port_str.isdigit() else 8000

    UI.step(5, 7, "Integrations")
    if not getattr(args, "ci", False):
        _setup_integrations()

    UI.step(6, 7, "Cognitive Indexer")
    print("  Choose which areas W.A.D.E. is allowed to scan.\n")
    
    zones = ["core"]
    if UI.confirm("  Index system documents? (OneDrive Documents/Desktop)", default=True):
        zones.append("system")
    if UI.confirm("  Index registered project directories?", default=True):
        zones.append("projects")
    
    custom_dirs = []
    if UI.confirm("  Add any custom directories now?", default=False):
        while True:
            p = UI.ask("  Path (or Enter to finish)").strip()
            if not p: break
            custom_dirs.append(p)

    UI.step(7, 7, "Finalizing")

    cfg = _build_config(hw, custom_llm, custom_stt, enable_voice, enable_web, port,
                        preferred_browser=preferred_browser,
                        automation_browser=automation_browser)
    cfg["user_name"] = user_name
    cfg["assistant_name"] = assistant_name
    cfg["llm"] = {"provider": llm_provider}
    cfg["indexer"] = {
        "enabled_zones": zones,
        "custom_dirs": custom_dirs
    }
    ConfigManager.save(cfg)
    UI.ok(f"Configuration written to {CONFIG_FILE}")

    print(f"\n  {UI.DIM}Initialising cognitive workspace...{UI.RESET}")
    from app.workspace import generate_cognitive_architecture
    generate_cognitive_architecture()
    UI.ok(f"Workspace established at {DUCK_HOME / 'workspace'}")

    if download_models:
        print(f"\n  {UI.DIM}Pulling model weights. Ctrl+C to defer, then run 'wade fit'.{UI.RESET}\n")
        try:
            _run_async_safely(_download_models())
        except KeyboardInterrupt:
            UI.warn("Download interrupted. Run 'wade fit' to resume.")
        except Exception as e:
            UI.error(f"Download error: {e}")
            UI.info("Run 'wade fit' later to retry.")

    if enable_web:
        _install_playwright(automation_browser)

    UI.header("W.A.D.E. IS ONLINE", "Setup Complete")

    print(textwrap.dedent(f"""\
      {UI.BOLD}Commands:{UI.RESET}
        {UI.GREEN}wade start{UI.RESET}   Start the core daemon
        {UI.GREEN}wade ui{UI.RESET}      Open the web interface
        {UI.GREEN}wade talk{UI.RESET}    Enter voice mode

      {UI.BOLD}Web Interface:{UI.RESET} {UI.CYAN}http://localhost:{port}/ui{UI.RESET}
    """))

    _run_async_safely(_run_preflight())

    if UI.confirm("Start W.A.D.E. now?", default=True):
        print()
        from app.daemon import start_daemon
        start_daemon()

async def _download_models() -> None:
    """Triggers the model discovery and download pipeline."""
    try:
        from app.services.model_manager import fit_and_install_models
        await fit_and_install_models()
    except ImportError:
        UI.error("LLM dependencies missing. Run: pip install wade-ai[llm]")

def _install_playwright(automation_browser: str = "chromium") -> None:
    """Installs Playwright browser binaries if the package is available."""
    import subprocess
    print(f"\n  {UI.DIM}Provisioning {automation_browser} browser binaries...{UI.RESET}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", automation_browser],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            UI.ok(f"{automation_browser.capitalize()} engine provisioned successfully.")
        else:
            UI.error(f"Browser installation failed: {result.stderr.strip()}")
            UI.info(f"To fix, run: python -m playwright install {automation_browser}")
    except FileNotFoundError:
        UI.warn("Playwright framework not found. Install via: pip install wade-ai[web]")

if __name__ == "__main__":
    run_wizard()