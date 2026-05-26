import re
import os
import sys
import time
import queue
import httpx
import ctypes
import shutil
import asyncio
import argparse
import threading
import subprocess

from typing import Any
from pathlib import Path

from app.setup_wizard import run_wizard
from app.core.browser_launcher import open_ui
from app.workspace import ensure_workspace_exists
from app.core.config import ConfigManager, LOG_FILE, PID_FILE
from app.daemon import start_daemon, stop_daemon, is_running

from app.core.version import VERSION

def _w(msg: str) -> None:
    print(f"[wade] {msg}")

def _ok(msg: str) -> None:
    print(f"[wade] ✔  {msg}")

def _err(msg: str) -> None:
    print(f"[wade] ✘  {msg}", file=sys.stderr)

def _info(label: str, value: str) -> None:
    print(f"[wade] {label:<8}{value}")

def is_admin() -> bool:
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def require_admin() -> None:
    if is_admin():
        return

    _w("Requesting elevated privileges...")

    script = sys.argv[0]
    params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
    current_dir = os.getcwd()

    if script.lower().endswith(".py"):
        executable = sys.executable
        arguments = f'"{os.path.abspath(script)}" {params}'
    else:
        executable = shutil.which(script) or script
        arguments = params

    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, arguments, current_dir, 1)

    if int(ret) > 32:
        sys.exit(0)
    else:
        _err("Elevation denied — cannot continue.")
        sys.exit(1)

def _build_uninstall_manifest() -> list[dict]:
    from app.core.config import (
        DUCK_HOME, CONFIG_FILE, DATA_DIR, WORKSPACE_DIR, LOG_FILE,
        MODEL_LOCK_FILE, PID_FILE, SKILLS_DIR, TASKS_DB_PATH, MONITORS_USER_DIR,
    )
    candidates = [
        {"label": "Config file",    "path": CONFIG_FILE,          "type": "file"},
        {"label": "Data directory", "path": DATA_DIR,              "type": "dir"},
        {"label": "Workspace",      "path": WORKSPACE_DIR,         "type": "dir"},
        {"label": "Memory",         "path": DUCK_HOME / "memory",  "type": "dir"},
        {"label": "Skills",         "path": SKILLS_DIR,            "type": "dir"},
        {"label": "Monitors",       "path": MONITORS_USER_DIR,     "type": "dir"},
        {"label": "Log file",       "path": LOG_FILE,              "type": "file"},
        {"label": "Tasks database", "path": TASKS_DB_PATH,         "type": "file"},
        {"label": "PID file",       "path": PID_FILE,              "type": "file"},
        {"label": "Models lock",    "path": MODEL_LOCK_FILE,       "type": "file"},
        {"label": "Wade home dir",  "path": DUCK_HOME,             "type": "dir"},
    ]
    manifest = [item for item in candidates if item["path"].exists()]
    if sys.platform == "win32":
        for task_name in ("WADE_GodMode_Start", "WADE_GodMode_Stop"):
            res = subprocess.run(
                ["schtasks", "/query", "/tn", task_name],
                capture_output=True,
                timeout=5,
            )
            if res.returncode == 0:
                manifest.append({"label": task_name, "path": None, "type": "task"})
    return manifest

def _confirm_uninstall(manifest: list[dict], remove_package: bool) -> bool:
    print()
    _w("The following will be permanently removed:")
    print()
    for item in manifest:
        if item["type"] == "task":
            print(f"  Scheduled Task: {item['label']}")
        else:
            print(f"  {item['path']}")
    if remove_package:
        print(f"  pip packages:   wade-ai, wade")
    print()
    _w("This cannot be undone.")
    answer = input("Proceed? [y/N]: ").strip().lower()
    return answer == "y"

def _execute_uninstall(manifest: list[dict], remove_package: bool) -> None:
    for item in manifest:
        try:
            if item["type"] == "file":
                item["path"].unlink(missing_ok=True)
                _ok(f"Removed {item['path']}")
            elif item["type"] == "dir":
                shutil.rmtree(item["path"], ignore_errors=True)
                _ok(f"Removed {item['path']}")
            elif item["type"] == "task":
                res = subprocess.run(
                    ["schtasks", "/delete", "/tn", item["label"], "/f"],
                    capture_output=True,
                )
                if res.returncode == 0:
                    _ok(f"Removed task {item['label']}")
                else:
                    _err(f"Could not remove task {item['label']}")
        except Exception as e:
            _err(f"Could not remove {item['label']}: {e}")

    if remove_package:
        for pkg in ["wade-ai", "wade"]:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "uninstall", pkg, "-y"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                _ok(f"Removed {pkg} package")
            else:
                _err(f"pip uninstall failed for {pkg}. Run manually: pip uninstall {pkg}")

def handle_uninstall(args):
    if is_running():
        _w("Stopping W.A.D.E...")
        try:
            stop_daemon()
        except Exception as e:
            _w(f"Warning: could not stop daemon ({e}), continuing with uninstall.")

    manifest = _build_uninstall_manifest()
    if not manifest:
        _ok("Nothing to remove.")
        return

    answer = input("Also remove the wade-ai package? [y/N]: ").strip().lower()
    remove_package = answer == "y"

    if not _confirm_uninstall(manifest, remove_package):
        _w("Uninstall cancelled.")
        return

    _execute_uninstall(manifest, remove_package)
    _ok("W.A.D.E. has been uninstalled.")
    if not remove_package:
        _w("The 'wade' command is still available. Run 'wade setup' to start fresh.")

def handle_godmode(args):
    """Install W.A.D.E. as silent Scheduled Tasks so future start/stop skip UAC."""
    require_admin()

    _w("Registering silent startup tasks...")

    script_path = os.path.abspath(__file__)
    python_exe = sys.executable

    cmd_start = (
        f'schtasks /create /tn "WADE_GodMode_Start" '
        f'/tr "\\"{python_exe}\\" \\"{script_path}\\" start" '
        f'/rl HIGHEST /sc onlogon /f'
    )
    cmd_stop = (
        f'schtasks /create /tn "WADE_GodMode_Stop"  '
        f'/tr "\\"{python_exe}\\" \\"{script_path}\\" stop"  '
        f'/rl HIGHEST /sc once /st 00:00 /f'
    )

    res1 = subprocess.run(cmd_start, shell=True, capture_output=True, text=True)
    res2 = subprocess.run(cmd_stop,  shell=True, capture_output=True, text=True)

    if res1.returncode == 0 and res2.returncode == 0:
        _ok("God Mode installed.")
        _w("'wade start' and 'wade stop' will now run silently with elevated privileges.")
    else:
        _err("Installation failed.")
        if res1.stderr:
            _err(f"start: {res1.stderr.strip()}")
        if res2.stderr:
            _err(f"stop:  {res2.stderr.strip()}")

_PLAYWRIGHT_SENTINEL = Path.home() / ".wade" / ".playwright_ready"

def _ensure_playwright_browsers() -> None:
    """Install Playwright browser binaries on first run — one-time, silent after that."""
    if _PLAYWRIGHT_SENTINEL.exists():
        return
    _w("First-run: installing browser engine (this takes ~30 seconds, once only)...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            _PLAYWRIGHT_SENTINEL.touch()
            _ok("Browser engine ready.")
        else:
            _w("Browser engine install failed — web skills will be unavailable.")
            _w("Fix manually: playwright install chromium")
            if result.stderr:
                _w(result.stderr.strip())
    except FileNotFoundError:
        _w("playwright not found — web skills will be unavailable.")
        _w("Fix: pip install playwright && playwright install chromium")
    except subprocess.TimeoutExpired:
        _w("Browser engine install timed out — web skills may be unavailable.")

def handle_start(args):
    _ensure_playwright_browsers()
    if is_admin():
        start_daemon()
    else:
        res = subprocess.run('schtasks /run /tn "WADE_GodMode_Start"', shell=True, capture_output=True)
        if res.returncode == 0:
            _w("Starting via scheduled task...")
        else:
            require_admin()
            start_daemon()

def handle_stop(args):
    if is_admin():
        stop_daemon()
    else:
        res = subprocess.run('schtasks /run /tn "WADE_GodMode_Stop"', shell=True, capture_output=True)
        if res.returncode == 0:
            _w("Stopping via scheduled task...")
        else:
            require_admin()
            stop_daemon()

def handle_restart(args):
    if is_admin():
        _w("Restarting...")
        stop_daemon()
        start_daemon()
    else:
        res_stop = subprocess.run('schtasks /run /tn "WADE_GodMode_Stop"', shell=True, capture_output=True)
        if res_stop.returncode == 0:
            print("[wade] Stopping", end="", flush=True)
            for _ in range(30):
                if not is_running():
                    break
                print(".", end="", flush=True)
                time.sleep(0.5)
            print()
            res_start = subprocess.run('schtasks /run /tn "WADE_GodMode_Start"', shell=True, capture_output=True)
            if res_start.returncode == 0:
                _w("Starting...")
            else:
                _err("Failed to start after stop. Run 'wade godmode' to reconfigure.")
        else:
            require_admin()
            stop_daemon()
            start_daemon()

def handle_status(args):
    running = is_running()
    cfg = ConfigManager.get()
    port = cfg.get("port", 8000)
    provider = cfg.get("llm", {}).get("provider", "ollama")
    
    if running:
        _info("Status", "online")
        _info("URL", f"http://127.0.0.1:{port}/ui")
        _info("Engine", provider.capitalize())
        if PID_FILE.exists():
            try:
                _info("PID", PID_FILE.read_text().strip())
            except Exception:
                pass
        models = cfg.get("models", {})
        primary = models.get("tools") or models.get("fast", "")
        if primary:
            _info("Model", primary)
    else:
        _info("Status", "offline")
        _w("Run 'wade start' to launch.")

def handle_ui(args):
    if not is_running():
        _err("W.A.D.E. is not running. Start it first with 'wade start'.")
        return
    port = ConfigManager.get().get("port", 8000)
    url = f"http://127.0.0.1:{port}/ui"
    browser = getattr(args, "browser", None)
    threading.Thread(target=lambda: open_ui(url, browser), daemon=True).start()
    _w(f"Opening {url}")

def handle_fit(args):
    from app.services.ollama_manager import ollama_manager

    async def _pull_configured_models():
        await ollama_manager.ensure_running()
        models = ConfigManager.get().get("models", {})
        if not models:
            _err("No models configured. Run 'wade setup' first.")
            return
        unique = list(dict.fromkeys(models.values()))
        try:
            result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
            installed = result.stdout.lower()
        except Exception:
            installed = ""
        for model in unique:
            if model.lower() in installed:
                _ok(model)
            else:
                _w(f"Pulling {model}...")
                await ollama_manager.ensure_model_pulled(model)
                _ok(model)
        _w("All models ready.")

    asyncio.run(_pull_configured_models())

def handle_config(args):
    cfg = ConfigManager.get()
    llm_cfg: dict[str, Any] = cfg.get("llm", {"provider": "ollama"})

    if args.provider:
        llm_cfg["provider"] = args.provider
        _ok(f"Provider set to {args.provider}")

    if args.model:
        p = llm_cfg.get("provider", "ollama")
        llm_cfg.setdefault(p, {})["model"] = args.model
        _ok(f"Model for {p} set to {args.model}")

    from app.core.credentials import CredentialsManager
    if getattr(args, "openai_key", None):
        CredentialsManager.save("openai", {"api_key": args.openai_key})
        _ok("OpenAI key saved.")
    if getattr(args, "gemini_key", None):
        CredentialsManager.save("gemini", {"api_key": args.gemini_key})
        _ok("Gemini key saved.")
    if getattr(args, "anthropic_key", None):
        CredentialsManager.save("anthropic", {"api_key": args.anthropic_key})
        _ok("Anthropic key saved.")

    if getattr(args, "name", None) is not None:
        name = args.name.strip()
        if not name:
            _err("Assistant name cannot be empty.")
        else:
            cfg["assistant_name"] = name
            _ok(f'Assistant name set to "{name}".')

    cfg["llm"] = llm_cfg
    ConfigManager.save(cfg)
    _ok("Configuration saved.")

def handle_logs(args):
    if not LOG_FILE.exists():
        _err(f"No log file found at {LOG_FILE}")
        return
    n = args.lines
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-n:]:
            print(line)
        if args.follow:
            _w("Following log (Ctrl+C to stop)...")
            with LOG_FILE.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.2)
    except KeyboardInterrupt:
        pass

def handle_version(args):
    _w(f"v{VERSION}")
    try:
        import platform
        _info("Python", platform.python_version())
        models = ConfigManager.get().get("models", {})
        primary = models.get("tools") or models.get("fast", "")
        if primary:
            _info("Model", primary)
    except Exception:
        pass

def handle_pair(args):
    """Run the WhatsApp bridge in the foreground so the user can scan the QR code."""
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    bridge_dir = str(PROJECT_ROOT / "deploy" / "docker")
    bridge_script = "whatsapp-bridge.js"

    _w("Starting WhatsApp pairing mode...")
    _w("Waiting for bridge to initialize (this may take a few seconds)...")

    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"

    if not os.path.exists(os.path.join(bridge_dir, "node_modules")):
        _w("Installing required Node.js dependencies...")
        try:
            subprocess.run(
                [npm_cmd, "install", "@whiskeysockets/baileys", "express", "axios", "qrcode-terminal", "pino"],
                cwd=bridge_dir,
                timeout=120,
            )
        except FileNotFoundError:
            _w("ERROR: npm not found. Please install Node.js (https://nodejs.org) and try again.")
            return
        except subprocess.TimeoutExpired:
            _w("ERROR: npm install timed out. Check your internet connection and try again.")
            return

    try:
        subprocess.run(["node", bridge_script], cwd=bridge_dir)
    except FileNotFoundError:
        _w("ERROR: node not found. Please install Node.js (https://nodejs.org) and try again.")
    except KeyboardInterrupt:
        print()
        _w("Pairing session closed.")

def handle_talk(args):
    """Activate voice interaction mode."""
    from app.services.voice import get_voice_service
    voice = get_voice_service()
    port = ConfigManager.get().get("port", 8000)

    _SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])["\']?\s+')

    def _extract_sentences(buffer: str):
        parts = _SENTENCE_BOUNDARY.split(buffer)
        if len(parts) <= 1:
            return [], buffer
        return parts[:-1], parts[-1]

    def _tts_worker(sentence_q: queue.Queue):
        while True:
            sentence = sentence_q.get()
            if sentence is None:
                break
            voice.speak(sentence)

    print()
    print("[wade] Voice interface active. Say 'Wade' to begin. Ctrl+C to exit.")
    print()

    try:
        while True:
            if voice.listen_for_wake_word(keyword="wade"):
                user_text = voice.listen()

                if not user_text:
                    _w("Listening again...")
                    continue

                print(f"[wade] You:   {user_text}")

                system_instruction = (
                    f"You are {ConfigManager.get_assistant_name()}, a highly capable AI voice assistant. "
                    "Keep your answers conversational, natural, and extremely concise (1 to 2 sentences max). "
                    "Do not use lists, bullet points, or markdown formatting."
                )
                full_prompt = f"{system_instruction}\n\nUser said: {user_text}"

                sentence_q: queue.Queue = queue.Queue()
                tts_thread = threading.Thread(target=_tts_worker, args=(sentence_q,), daemon=True)
                tts_thread.start()

                try:
                    with httpx.Client(timeout=None) as client:
                        with client.stream(
                            "POST",
                            f"http://localhost:{port}/api/chat",
                            json={"prompt": full_prompt},
                        ) as response:
                            response.raise_for_status()
                            buffer = ""
                            print("[wade] W.A.D.E:", end=" ", flush=True)
                            for chunk in response.iter_text():
                                print(chunk, end="", flush=True)
                                buffer += chunk
                                sentences, buffer = _extract_sentences(buffer)
                                for s in sentences:
                                    if s.strip():
                                        sentence_q.put(s.strip())
                            if buffer.strip():
                                sentence_q.put(buffer.strip())
                            print()
                except httpx.ConnectError:
                    _err("W.A.D.E. is offline. Run 'wade start' first.")
                    sentence_q.put("My gateway is currently offline. Please start the service.")
                except Exception as e:
                    _err(f"Gateway error: {e}")
                finally:
                    sentence_q.put(None)
                    tts_thread.join()

                print()
                _w("Listening again...")

    except KeyboardInterrupt:
        print()
        _w("Voice interface closed.")

def handle_setup(args):
    """Run the interactive first-time setup wizard."""
    if not args.ci:
        run_wizard(reinstall=True, advanced=getattr(args, "advanced", False))
        _ensure_playwright_browsers()
    else:
        _w("CI mode: skipping model downloads.")
        from app.core.config import CONFIG_FILE
        if not CONFIG_FILE.exists():
            ConfigManager.save({"llm": {"provider": "ollama"}, "ci_mode": True})
            _ok(f"Config written to {CONFIG_FILE}")
        else:
            _w(f"Config already exists at {CONFIG_FILE}")

COMMANDS = {
    "setup":   handle_setup,
    "start":   handle_start,
    "stop":    handle_stop,
    "restart": handle_restart,
    "status":  handle_status,
    "ui":      handle_ui,
    "fit":     handle_fit,
    "config":  handle_config,
    "logs":    handle_logs,
    "pair":    handle_pair,
    "talk":      handle_talk,
    "version":   handle_version,
    "godmode":   handle_godmode,
    "uninstall": handle_uninstall,
}

def main():
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleTitleW("W.A.D.E.")

    parser = argparse.ArgumentParser(
        prog="wade",
        description="W.A.D.E. — Your local, private AI assistant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="First time?  Run:  wade setup",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    setup_p = subparsers.add_parser("setup",   help="First-time setup wizard — start here")
    setup_p.add_argument(
        "--ci",
        action="store_true",
        help="Non-interactive CI mode: verify config only, skip model downloads.",
    )
    setup_p.add_argument(
        "--advanced",
        action="store_true",
        help="Full configuration wizard with voice, web browsing, and integrations.",
    )
    subparsers.add_parser("start",   help="Start W.A.D.E. in the background")
    subparsers.add_parser("stop",    help="Stop W.A.D.E.")
    subparsers.add_parser("restart", help="Restart W.A.D.E.")
    subparsers.add_parser("status",  help="Show running status and active model")
    ui_p = subparsers.add_parser("ui", help="Open the web interface in your browser")
    ui_p.add_argument(
        "--browser",
        choices=["chrome", "firefox", "edge", "safari", "opera"],
        default=None,
        help="Browser to open the UI in (overrides preferred_browser config)",
    )
    subparsers.add_parser("talk",    help="Enter voice interaction mode")
    subparsers.add_parser("fit",     help="Pull best-fit models for your hardware")
    subparsers.add_parser("pair",    help="Pair W.A.D.E. with WhatsApp (scan QR)")
    subparsers.add_parser("version", help="Show version information")
    subparsers.add_parser("godmode",    help="Install as a silent background service (Windows, requires admin)")
    subparsers.add_parser("uninstall",  help="Remove W.A.D.E. data and optionally the package")

    logs_p = subparsers.add_parser("logs", help="Show recent log output")
    logs_p.add_argument("-n", "--lines", type=int, default=50, metavar="N", help="Number of lines to show (default: 50)")
    logs_p.add_argument("-f", "--follow", action="store_true", help="Follow log output in real time")

    config_p = subparsers.add_parser("config", help="Update configuration values")
    config_p.add_argument("--provider", choices=["ollama", "openai", "gemini", "anthropic"], help="Set LLM provider")
    config_p.add_argument("--model", help="Set specific model name")
    config_p.add_argument("--openai-key", help="Set OpenAI API key")
    config_p.add_argument("--gemini-key", help="Set Gemini API key")
    config_p.add_argument("--anthropic-key", help="Set Anthropic API key")
    config_p.add_argument("--name", help="Set assistant name (e.g. 'Jarvis')")

    args = parser.parse_args()

    if args.command not in ("setup", "version", "uninstall") and not ConfigManager.is_configured():
        _w("Not configured yet. Starting setup wizard...")
        print()
        run_wizard(reinstall=False)
        return

    if args.command not in ("setup", "version", "uninstall"):
        ensure_workspace_exists()

    if args.command in COMMANDS:
        COMMANDS[args.command](args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()