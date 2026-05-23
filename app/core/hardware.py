import re
import time
import psutil
import shutil
import asyncio
import platform
import datetime
import threading
import urllib.request

from pathlib import Path

from typing import List, Dict, Any
from dataclasses import dataclass, asdict

from app.core.utils import run_command

GiB = 1024**3

@dataclass
class Device:
    kind: str 
    vendor: str 
    name: str
    backend: str
    memory_total_gb: float 
    memory_free_gb: float  
    memory_usable_gb: float
    memory_type: str
    meta: Dict[str, Any]

def probe_hardware() -> Dict[str, Any]:
    """Probes the host system for hardware information, including GPU details and available RAM. Returns a structured dictionary of results."""
    os_name = platform.system()
    total_ram_gb = psutil.virtual_memory().total / GiB
    avail_ram_gb = psutil.virtual_memory().available / GiB
    devices: List[Device] = []

    # --- NVIDIA (CUDA) ---
    if shutil.which("nvidia-smi"):
        out, err, code = run_command(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,temperature.gpu", "--format=csv,nounits,noheader"], timeout=5)
        if out:
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    total_gb = int(parts[1]) / 1024
                    free_gb = int(parts[2]) / 1024
                    temp = parts[3]
                    devices.append(Device(
                        kind="gpu", vendor="nvidia", name=parts[0], backend="cuda",
                        memory_total_gb=round(total_gb, 2),
                        memory_free_gb=round(free_gb, 2),
                        memory_usable_gb=round(min(total_gb * 0.85, free_gb), 2),
                        memory_type="dedicated", meta={"temperature": temp}
                    ))

    # --- AMD (ROCm) ---
    if shutil.which("rocm-smi"):
        out, err, code = run_command(["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--showtemp"], timeout=5)
        if out:
            total_matches = re.findall(r"Total Memory \(B\):\s*(\d+)", out)
            used_matches = re.findall(r"Used Memory \(B\):\s*(\d+)", out)
            temp_matches = re.findall(r"Temperature \(Sensor edge\) \(C\):\s*(\d+\.?\d*)", out)
            for i, total_b in enumerate(total_matches):
                total_gb = int(total_b) / GiB
                free_gb = total_gb - (int(used_matches[i]) / GiB) if i < len(used_matches) else -1.0
                temp = temp_matches[i] if i < len(temp_matches) else "N/A"
                devices.append(Device(
                    kind="gpu", vendor="amd", name=f"AMD GPU {i}", backend="rocm",
                    memory_total_gb=round(total_gb, 2),
                    memory_free_gb=round(free_gb, 2),
                    memory_usable_gb=round(total_gb * 0.85 if free_gb < 0 else min(total_gb * 0.85, free_gb), 2),
                    memory_type="dedicated", meta={"temperature": temp}
                ))

    # --- APPLE SILICON (Metal) ---
    if os_name == "Darwin" and platform.machine().lower() in ("arm64", "aarch64"):
        usable = total_ram_gb * 0.70
        devices.append(Device(
            kind="gpu", vendor="apple", name="Apple Silicon GPU", backend="metal",
            memory_total_gb=round(total_ram_gb, 2),
            memory_free_gb=round(avail_ram_gb, 2),
            memory_usable_gb=round(min(usable, avail_ram_gb), 2),
            memory_type="unified", meta={}
        ))

    # --- INTEL ARC / INTEGRATED ---
    if not any(d.vendor in ["nvidia", "amd", "apple"] for d in devices):
        devices.append(Device(
            kind="gpu" if os_name == "Linux" else "cpu", 
            vendor="intel", name="Intel Graphics (Shared)", backend="cpu",
            memory_total_gb=round(total_ram_gb, 2),
            memory_free_gb=round(avail_ram_gb, 2),
            memory_usable_gb=round(avail_ram_gb * 0.50, 2),
            memory_type="shared", meta={}
        ))

    # --- NPU DETECTION (2026 Trends) ---
    if os_name == "Windows":
        try:
            cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance -Namespace root/cimv2 -ClassName Win32_PnPEntity | Where-Object { $_.Name -match 'NPU|Neural|AI Boost|IPU' } | Select-Object -ExpandProperty Name"]
            out, err, code = run_command(cmd, timeout=5)
            if out:
                for line in out.strip().splitlines():
                    devices.append(Device(
                        kind="npu", vendor="integrated", name=line.strip(), backend="npu",
                        memory_total_gb=0, memory_free_gb=0, memory_usable_gb=0,
                        memory_type="unified", meta={"task_optimized": "stt,tts"}
                    ))
        except Exception:
            pass

    # Linux: Check for acceleration drivers
    if os_name == "Linux":
        accel_path = Path("/sys/class/accel")
        if accel_path.exists():
            for accel_dev in accel_path.iterdir():
                try:
                    name = (accel_dev / "device/model").read_text().strip()
                except Exception:
                    name = f"NPU Accelerator ({accel_dev.name})"
                devices.append(Device(
                    kind="npu", vendor="integrated", name=name, backend="npu",
                    memory_total_gb=0, memory_free_gb=0, memory_usable_gb=0,
                    memory_type="unified", meta={"task_optimized": "stt,tts"}
                ))

    # Apple: Apple Neural Engine (ANE)
    if os_name == "Darwin" and any(d.vendor == "apple" for d in devices):
        devices.append(Device(
            kind="npu", vendor="apple", name="Apple Neural Engine (ANE)", backend="metal",
            memory_total_gb=0, memory_free_gb=0, memory_usable_gb=0,
            memory_type="unified", meta={"task_optimized": "stt,tts,vision"}
        ))

    cpu_meta = {}
    try:
        _sensors_fn = getattr(psutil, "sensors_temperatures", None)
        if _sensors_fn:
            temps = _sensors_fn()
            if temps:
                for name, entries in temps.items():
                    if name.lower() in ('coretemp', 'cpu_thermal', 'k10temp', 'zenpower'):
                        cpu_meta["temperature"] = entries[0].current
                        break
    except Exception:
        pass

    devices.append(Device(
        kind="cpu", vendor="unknown", name=platform.processor() or "CPU", backend="cpu",
        memory_total_gb=round(total_ram_gb, 2),
        memory_free_gb=round(avail_ram_gb, 2),
        memory_usable_gb=round(avail_ram_gb * 0.80, 2),
        memory_type="shared", meta=cpu_meta
    ))

    def rank(d: Device) -> int:
        return {"cuda": 4, "rocm": 3, "metal": 2, "cpu": 0}.get(d.backend, 0)

    primary = max(devices, key=lambda d: (rank(d), d.memory_usable_gb))

    return {
        "os": os_name,
        "total_ram_gb": round(total_ram_gb, 2),
        "primary": asdict(primary),
        "devices": [asdict(d) for d in devices]
    }

class SystemEnvironment:
    """Aggressively cached environment manager with background async updating."""
    def __init__(self):
        self.location = "Detecting..."
        self.weather = "Fetching..."
        self.net_last_update = 0.0
        self.cpu_cache = 0.0
        self.cpu_last_update = 0.0
        self._update_lock = asyncio.Lock()
        self.os_info = f"{platform.system()} {platform.release()}"
        self.hardware_profile = self._build_hardware_string()
        
        threading.Thread(target=self._fetch_network_data, daemon=True).start()

    def _build_hardware_string(self) -> str:
        """Runs once to build a static string of the host's physical hardware."""
        try:
            hw_data = probe_hardware()
            primary_gpu = hw_data.get("primary", {})
            cpu_name = platform.processor() or "Unknown CPU"
            gpu_str = f"{primary_gpu.get('name', 'Unknown GPU')} ({primary_gpu.get('memory_total_gb', 0)}GB VRAM)"
            return f"{gpu_str} | {cpu_name}"
        except Exception:
            return "Hardware Profile Unavailable"

    async def get_context(self) -> str:
        """Returns the real-time system context string. Guaranteed non-blocking."""
        now_ts = time.time()
        
        if now_ts - self.net_last_update > 1800:
            if not self._update_lock.locked():
                asyncio.create_task(self._async_update_wrapper())
            
        if now_ts - self.cpu_last_update > 5.0:
            self.cpu_cache = await asyncio.to_thread(psutil.cpu_percent, interval=0.1)
            self.cpu_last_update = time.time()

        mem_avail = psutil.virtual_memory().available / (1024**3)
        now_str = datetime.datetime.now().strftime('%A, %B %d, %Y %I:%M %p')
        
        return (
            f"Date/Time: {now_str} | OS: {self.os_info} | "
            f"CPU: {self.cpu_cache}% | RAM: {mem_avail:.1f}GB Free | "
            f"Loc: {self.location} | Wx: {self.weather}\n"
            f"Host Hardware: {self.hardware_profile}"
        )

    async def _async_update_wrapper(self):
        """Async wrapper to run the blocking network fetch in a thread."""
        async with self._update_lock:
            await asyncio.to_thread(self._fetch_network_data)
            self.net_last_update = time.time()

    def _fetch_network_data(self):
        """Standard blocking I/O fetch. Safe to run in a background thread."""
        try:
            req = urllib.request.Request(
                "https://wttr.in/?format=%l:+%C+%t", 
                headers={'User-Agent': 'WADE-System-Agent/1.0'}
            )

            with urllib.request.urlopen(req, timeout=3.5) as response:
                res = response.read().decode('utf-8').strip()
                if ":" in res:
                    loc, wx = res.split(":", 1)
                    self.location = loc.strip()
                    self.weather = wx.strip()
                else:
                    self.weather = res
                    self.location = "Detected via IP"
        except Exception as e:
            print(f"⚠️ Background Uplink Fetch Failed: {e}")

system_environment = SystemEnvironment()