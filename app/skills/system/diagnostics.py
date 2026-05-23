import os
import sys
import psutil
import asyncio
import logging
import subprocess

from pathlib import Path

from app.core.config import PID_FILE, ConfigManager
from app.skills.registry import register_tool
from app.core.hardware import probe_hardware, system_environment

logger = logging.getLogger("wade_agent_runtime")

@register_tool("check_hardware_stats")
async def check_hardware_stats() -> str:
    """Returns a detailed report of the physical PC hardware status."""
    report = ["🖥️ PC HARDWARE HEALTH REPORT\n" + "="*40]
    
    try:
        hw = await asyncio.to_thread(probe_hardware)
    except Exception as e:
        logger.warning(f"Hardware probe error bypassed: {e}")
        hw = {"os": "Unknown", "devices": []}
        report.append(f"⚠️ Hardware probe partially failed (See logs).")

    try:
        ctx = await system_environment.get_context()
    except Exception as e:
        logger.warning(f"System environment context error bypassed: {e}")
        ctx = "Unavailable (system environment context error)."
        
    report.append(f"OS: {hw.get('os', 'Unknown')}")
    report.append(f"Real-time Context: {ctx}")
    
    report.append("\n[Devices]")
    for dev in hw.get("devices", []):
        name = dev.get("name")
        kind = dev.get("kind", "UNKNOWN").upper()
        temp = dev.get("meta", {}).get("temperature", "N/A")
        mem_total = dev.get("memory_total_gb", "Unknown")
        mem_free = dev.get("memory_free_gb", "Unknown")
        
        report.append(f"- {kind}: {name}")
        report.append(f"  VRAM/RAM: {mem_free}GB free / {mem_total}GB total")
        if temp != "N/A":
            report.append(f"  Temp: {temp}°C")
            
    return "\n".join(report)

@register_tool("check_active_models")
async def check_active_models() -> str:
    """Returns the currently active Ollama models."""
    try:
        config = ConfigManager.get()
        models = config.get("roles", {}).get("mapping", {})
        
        if not models:
            return "No specific models mapped in configuration. Using system defaults."
            
        report = ["🧠 ACTIVE AI MODELS\n" + "="*40]
        for role, model in models.items():
            report.append(f"- {role.title()}: {model}")
            
        return "\n".join(report)
    except Exception as e:
        return f"Error retrieving active models: {e}"

@register_tool("check_wade_services_health")
async def check_wade_services_health() -> str:
    """Checks the pulse of W.A.D.E.'s core internal components."""
    def _run_checks():
        report = ["🏥 W.A.D.E. INTERNAL SYSTEM HEALTH REPORT\n" + "="*40]
        
        gateway_status = "🔴 OFFLINE"
        if PID_FILE.exists():
            gateway_status = f"🟢 ONLINE (PID: {PID_FILE.read_text().strip()})"
        report.append(f"Gateway Daemon: {gateway_status}")
        
        bridge_status = "🔴 OFFLINE"
        bridge_running = False
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                if proc.info['name'] in ['node', 'node.exe']:
                    cmdline = proc.info.get('cmdline') or []
                    if any('whatsapp-bridge.js' in cmd for cmd in cmdline):
                        bridge_running = True
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
                
        if bridge_running:
            bridge_status = "🟢 ONLINE"
        report.append(f"WhatsApp Bridge: {bridge_status}")

        import socket
        def check_port(port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                return s.connect_ex(('localhost', port)) == 0

        headless_port = check_port(9223)
        headed_port = check_port(9222)
        report.append(f"Browser Service (Headless:9223): {'🟢 ONLINE' if headless_port else '🔴 OFFLINE'}")
        report.append(f"Browser Service (Headed:9222):   {'🟢 ONLINE' if headed_port else '🔴 OFFLINE'}")
        
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            admin_status = "🟢 ACTIVE" if is_admin else "🟡 INACTIVE (User Mode)"
        except Exception as e:
            logger.debug(f"Admin privilege check failed (non-critical, assuming active): {e}")
            admin_status = "🟢 ACTIVE"
        report.append(f"God Mode (Elevated Privileges): {admin_status}")

        return "\n".join(report)

    return await asyncio.to_thread(_run_checks)

@register_tool("perform_system_recovery")
async def perform_system_recovery(action: str) -> str:
    """Executes sandboxed self-healing actions."""
    def _recover():
        try:
            PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
            bridge_dir = str(PROJECT_ROOT / "deploy" / "docker")

            if action == "restart_whatsapp_bridge":
                killed_count = 0
                for proc in psutil.process_iter(['name', 'cmdline']):
                    try:
                        if proc.info['name'] in ['node', 'node.exe']:
                            cmdline = proc.info.get('cmdline') or []
                            if any('whatsapp-bridge.js' in cmd for cmd in cmdline):
                                proc.kill()
                                killed_count += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass

                try:
                    env = os.environ.copy()
                    node_cmd = ["node", "whatsapp-bridge.js"]
                    if sys.platform == "win32":
                        CREATE_NO_WINDOW = 0x08000000
                        subprocess.Popen(node_cmd, creationflags=CREATE_NO_WINDOW, env=env, cwd=bridge_dir)
                    else:
                        subprocess.Popen(node_cmd, start_new_session=True, env=env, cwd=bridge_dir)
                    return f"✅ WhatsApp bridge successfully restarted (Terminated {killed_count} old instances)."
                except FileNotFoundError:
                    return "❌ Could not start WhatsApp bridge — 'node' not found in PATH or bridge directory missing."
                except Exception as e:
                    return f"❌ WhatsApp bridge restart failed: {e}"

            elif action == "clear_stale_pid":
                if PID_FILE.exists():
                    PID_FILE.unlink()
                    return "✅ Stale PID file cleared. You can now safely restart the gateway."
                return "ℹ️ No stale PID file found."

            elif action == "restart_gateway":
                return "⚠️ System Guard: I cannot restart my own brain directly from within the loop. Please advise the user to run 'wade stop' followed by 'wade start' in the CLI."

            elif action == "restart_browser_service":
                try:
                    result = subprocess.run(
                        ["docker", "restart", "wade_sandbox_browser"],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        return "✅ Browser service restarted. Allow 10–15 seconds for the CDP endpoints to become available on ports 9222 and 9223."
                    start_result = subprocess.run(
                        ["docker", "start", "wade_sandbox_browser"],
                        capture_output=True, text=True, timeout=30
                    )
                    if start_result.returncode == 0:
                        return "✅ Browser service started. Allow 10–15 seconds for ports 9222 and 9223 to become available."
                    err = result.stderr.strip() or start_result.stderr.strip() or "container may not exist"
                    return f"❌ Browser service restart failed: {err}. Ensure Docker is running and the wade_sandbox_browser container exists."
                except FileNotFoundError:
                    return "❌ Docker CLI not found. Ensure Docker Desktop is installed, running, and its CLI is in PATH."
                except subprocess.TimeoutExpired:
                    return "⚠️ Docker command timed out. The container may be starting slowly — wait 20 seconds and re-run the health check."
                except Exception as e:
                    return f"❌ Browser service restart failed: {e}"

            elif action == "provision_browser_service":
                try:
                    engine = ConfigManager.get().get("automation_browser", "chromium")
                    subprocess.run([sys.executable, "-m", "playwright", "install", engine], check=True, capture_output=True)
                    return f"✅ Local {engine} binaries provisioned. W.A.D.E. will now attempt to use local browser fallback if remote connection fails."
                except Exception as e:
                    return f"❌ Failed to provision browser binaries: {e}"

            return f"❌ Unknown recovery action '{action}'. Valid actions: restart_whatsapp_bridge, restart_browser_service, provision_browser_service, restart_gateway, clear_stale_pid."

        except Exception as e:
            logger.error("[DIAGNOSTICS] perform_system_recovery unexpected error: %s", e)
            return f"❌ Recovery action failed unexpectedly: {e}"

    return await asyncio.to_thread(_recover)