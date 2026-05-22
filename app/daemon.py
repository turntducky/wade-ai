import os
import sys
import time
import httpx
import signal
import shutil
import socket
import asyncio
import threading
import subprocess

from pathlib import Path

from app.workspace import ensure_workspace_exists
from app.core.config import LOG_FILE, BRIDGE_LOG_FILE, PID_FILE, ConfigManager

HEALTH_CHECK_RETRIES = 60
HEALTH_CHECK_INTERVAL = 0.5
HEALTH_CHECK_TIMEOUT = 1.0

def _find_free_port(start: int = 8000, max_tries: int = 20) -> int:
    """Return the first available TCP port starting from `start`."""
    import socket
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {start}–{start + max_tries}")

def is_running(client=None):
    try:
        port = ConfigManager.get().get("port", 80)
        url = f"http://127.0.0.1:{port}/health"
        if client:
            res = client.get(url)
        else:
            with httpx.Client(timeout=HEALTH_CHECK_TIMEOUT) as c:
                res = c.get(url)
        return res.status_code == 200
    except httpx.RequestError:
        return False

async def _ensure_models_ready():
    """Ensure Ollama is running and all configured models are present locally."""
    from app.services.ollama_manager import ollama_manager
    print("[wade] Ensuring Ollama is running...")
    await ollama_manager.ensure_running()

    config = ConfigManager.get()
    unique_models = list(dict.fromkeys(config.get("models", {}).values()))
    if not unique_models:
        print("[wade] No models configured. Run 'wade fit' to set up.")
        return

    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        installed = result.stdout.lower()
    except Exception:
        installed = ""

    for model in unique_models:
        if model.lower() in installed:
            print(f"[wade]   ✔  {model}")
        else:
            print(f"[wade]   ↓  Pulling {model}...")
            try:
                await ollama_manager.ensure_model_pulled(model)
                print(f"[wade]   ✔  {model}")
            except Exception as e:
                print(f"[wade]   !  Could not pull '{model}': {e}")

def start_daemon():
    ensure_workspace_exists()

    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
        try:
            backup = LOG_FILE.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            LOG_FILE.rename(backup)
        except Exception:
            LOG_FILE.unlink(missing_ok=True)

    if PID_FILE.exists() and not is_running():
        PID_FILE.unlink(missing_ok=True)

    if is_running():
        port = ConfigManager.get().get("port", 80)
        suffix = "/ui" if port == 80 else f":{port}/ui"
        print(f"[wade] Already running → http://127.0.0.1{suffix}  /  http://wade.local{suffix}")
        return

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore
    asyncio.run(_ensure_models_ready())

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    bridge_dir = str(PROJECT_ROOT / "deploy" / "docker")

    def _port_available(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            try:
                _s.bind(("0.0.0.0", p))
                return True
            except OSError:
                return False

    if _port_available(80):
        port = 80
    else:
        port = _find_free_port(8000)
        print("[wade] Port 80 unavailable — using port 8000.")
        print("[wade] For the clean wade.local/ui URL, run once as admin:")
        print("[wade]   netsh http add urlacl url=http://+:80/ user=Everyone")

    config = ConfigManager.get()
    config["port"] = port
    ConfigManager.save(config)

    bridge_env = env.copy()
    bridge_env["PYTHON_BASE_URL"] = f"http://localhost:{port}"

    if os.path.exists(os.path.join(bridge_dir, "whatsapp-bridge.js")):
        node_cmd = ["node", "whatsapp-bridge.js"]
        if not os.path.exists(os.path.join(bridge_dir, "node_modules")):
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            print("[wade] Installing WhatsApp bridge dependencies...")
            try:
                subprocess.run(
                    [npm_cmd, "install", "@whiskeysockets/baileys", "express", "axios", "qrcode-terminal", "pino"],
                    cwd=bridge_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                print("[wade] npm install timed out — skipping WhatsApp bridge.")
            except FileNotFoundError:
                print("[wade] npm not found — skipping WhatsApp bridge.")
        print(f"[wade] WhatsApp bridge starting in background (log: {BRIDGE_LOG_FILE})")
        with open(BRIDGE_LOG_FILE, "a", encoding="utf-8") as bridge_log:
            if sys.platform == "win32":
                CREATE_NO_WINDOW = 0x08000000
                subprocess.Popen(node_cmd, stdout=bridge_log, stderr=bridge_log, creationflags=CREATE_NO_WINDOW, env=bridge_env, cwd=bridge_dir)
            else:
                subprocess.Popen(node_cmd, stdout=bridge_log, stderr=bridge_log, start_new_session=True, env=bridge_env, cwd=bridge_dir)

    print("[wade] Launching server...")

    executable = sys.executable
    if sys.platform == "win32":
        wade_exe = os.path.join(os.path.dirname(executable), "WADE_Gateway.exe")
        if not os.path.exists(wade_exe):
            try:
                shutil.copy(executable, wade_exe)
            except Exception:
                pass

        if os.path.exists(wade_exe):
            executable = wade_exe

    with open(LOG_FILE, "a", encoding="utf-8") as log:
        cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(port)]
        if sys.platform == "win32":
            CREATE_NO_WINDOW = 0x08000000
            process = subprocess.Popen(cmd, stdout=log, stderr=log, creationflags=CREATE_NO_WINDOW, env=env, cwd=os.getcwd())
        else:
            process = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True, env=env, cwd=os.getcwd())

    def _fmt_url(host: str, p: int, path: str = "/ui") -> str:
        return f"http://{host}{path}" if p == 80 else f"http://{host}:{p}{path}"

    with httpx.Client(timeout=HEALTH_CHECK_TIMEOUT) as client:
        print("[wade] Waiting for server", end="", flush=True)
        for _ in range(HEALTH_CHECK_RETRIES):
            if is_running(client):
                print(" ready.")
                PID_FILE.write_text(str(process.pid))
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as _s:
                        _s.connect(("8.8.8.8", 80))
                        lan_ip = _s.getsockname()[0]
                except Exception:
                    lan_ip = "127.0.0.1"
                print(f"[wade] Online (PID {process.pid})")
                print(f"[wade]   Local  → {_fmt_url('127.0.0.1', port)}")
                print(f"[wade]   LAN    → {_fmt_url(lan_ip, port)}")
                print(f"[wade]   mDNS   → {_fmt_url('wade.local', port)}  (same WiFi only)")
                try:
                    import qrcode as _qr
                    _q = _qr.QRCode(border=1)
                    _q.add_data(_fmt_url(lan_ip, port))
                    _q.make(fit=True)
                    print("\n[wade] Scan on mobile (same WiFi):")
                    _q.print_ascii(invert=True)
                    print()
                except Exception:
                    pass
                if sys.platform == "win32":
                    try:
                        from app.core.browser_launcher import open_ui
                        open_ui(_fmt_url("127.0.0.1", port))
                    except Exception:
                        pass
                return
            print(".", end="", flush=True)
            time.sleep(HEALTH_CHECK_INTERVAL)
        print()

    timeout_s = HEALTH_CHECK_RETRIES * HEALTH_CHECK_INTERVAL
    print(f"[wade] Server did not become healthy within {timeout_s:.0f}s.")
    print(f"[wade] Check logs: {LOG_FILE}")
    process.kill()
    process.wait()

def _kill_port_win(port: int):
    """Kill any process listening on the given port (Windows)."""
    try:
        res = subprocess.run(f'netstat -ano | findstr :{port}', shell=True, capture_output=True, text=True)
        if res.stdout:
            for line in res.stdout.strip().split('\n'):
                if 'LISTENING' in line:
                    zpid = line.strip().split()[-1]
                    subprocess.run(["taskkill", "/F", "/T", "/PID", zpid], capture_output=True)
    except Exception:
        pass

def stop_daemon():
    port = ConfigManager.get().get("port", 8000)
    if is_running() and PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        print(f"[wade] Stopping server (PID {pid})...")
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                subprocess.run(["taskkill", "/F", "/IM", "WADE_Gateway.exe"], capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
                for _ in range(30):
                    try:
                        os.kill(pid, 0)
                        time.sleep(0.1)
                    except ProcessLookupError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
        except Exception as e:
            print(f"Shutdown warning: {e}")

    print("[wade] Cleaning up ports...")
    if sys.platform == "win32":
        t3000 = threading.Thread(target=_kill_port_win, args=(3000,))
        tport = threading.Thread(target=_kill_port_win, args=(port,))
        t3000.start()
        tport.start()
        t3000.join()
        tport.join()
    else:
        os.system("pkill -f 'node whatsapp-bridge.js'")

    PID_FILE.unlink(missing_ok=True)
    print("[wade] Stopped.")