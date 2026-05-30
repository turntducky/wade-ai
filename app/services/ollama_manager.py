from __future__ import annotations

import os
import sys
import httpx
import asyncio
import logging
import subprocess

logger = logging.getLogger("wade.ollama_manager")

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_STARTUP_TIMEOUT_S = 30

def _build_ollama_env() -> dict:
    """Build an environment dict for the Ollama subprocess, configuring optimal settings based on detected hardware. This includes setting thread counts for CPU inference and enabling GPU offloading and flash attention where appropriate. Falls back gracefully if hardware probing fails, allowing Ollama to use its own defaults."""
    env = os.environ.copy()
    try:
        import psutil
        from app.core.hardware import probe_hardware

        hw      = probe_hardware()
        primary = hw.get("primary", {})
        backend = primary.get("backend", "cpu")
        vram_gb = primary.get("memory_usable_gb", 0.0)

        phys_cores = psutil.cpu_count(logical=False) or 4
        thread_count = str(min(max(phys_cores, 2), 16))
        env["OLLAMA_NUM_THREAD"] = thread_count

        if backend in ("cuda", "rocm"):
            env["OLLAMA_NUM_GPU"]         = "999"
            env["OLLAMA_FLASH_ATTENTION"] = "1"
            env["OLLAMA_KV_CACHE_TYPE"]   = "q4_0" if vram_gb < 8 else "q8_0"
            if vram_gb >= 12:
                env["OLLAMA_MAX_LOADED_MODELS"] = "2"
                env["OLLAMA_NUM_PARALLEL"]      = "2"
            logger.info(
                "[OLLAMA] GPU config — backend=%s vram=%.1fGB kv_cache=%s threads=%s flash=%s",
                backend, vram_gb,
                env["OLLAMA_KV_CACHE_TYPE"], thread_count,
                env.get("OLLAMA_FLASH_ATTENTION", "0"),
            )

        elif backend == "metal":
            env["OLLAMA_NUM_GPU"]         = "999"
            env["OLLAMA_FLASH_ATTENTION"] = "1"
            env["OLLAMA_KV_CACHE_TYPE"]   = "q8_0"
            logger.info("[OLLAMA] Apple Silicon config — flash_attn=1 threads=%s", thread_count)

        else:
            logger.info("[OLLAMA] CPU-only config — threads=%s", thread_count)

    except Exception as exc:
        logger.warning("[OLLAMA] Hardware probe failed — using Ollama defaults: %s", exc)

    return env

class OllamaManager:
    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._we_started_it: bool = False

    async def is_running(self) -> bool:
        """Return True if the Ollama HTTP API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{OLLAMA_BASE_URL}/api/version")
                return resp.status_code == 200
        except Exception:
            return False

    async def ensure_running(self) -> None:
        """Start Ollama if not already running. Idempotent."""
        if await self.is_running():
            logger.info("[OLLAMA] Already running — using existing instance.")
            return

        logger.info("[OLLAMA] Not detected — spawning managed instance.")
        env = _build_ollama_env()
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env":    env,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True

        try:
            self._process = subprocess.Popen(["ollama", "serve"], **kwargs)
        except FileNotFoundError:
            raise RuntimeError(
                "Could not find 'ollama' on PATH. "
                "Install Ollama from https://ollama.com and ensure it is on your PATH."
            ) from None

        self._we_started_it = True

        for _ in range(OLLAMA_STARTUP_TIMEOUT_S):
            await asyncio.sleep(1)
            if await self.is_running():
                logger.info("[OLLAMA] Ready.")
                return

        raise RuntimeError(
            f"Ollama did not become ready within {OLLAMA_STARTUP_TIMEOUT_S}s. "
            "Ensure 'ollama' is on PATH and at least one model is downloaded "
            "('ollama pull qwen2.5:3b' to start)."
        )

    async def ensure_model_pulled(self, model: str) -> None:
        """Pull a model if it is not already available locally."""
        if await self.model_exists(model):
            logger.info("[OLLAMA] Model '%s' already present.", model)
            return
        logger.info("[OLLAMA] Pulling model '%s'...", model)
        print(f"📥 Pulling {model} (this may take several minutes — large models can be 5+ GB)...", flush=True)
        result = await asyncio.to_thread(subprocess.run, ["ollama", "pull", model])
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to pull model '{model}'. Check your internet connection and try: ollama pull {model}"
            )
        logger.info("[OLLAMA] Model '%s' ready.", model)

    async def model_exists(self, model: str) -> bool:
        """Return True if the model is listed in 'ollama list'."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["ollama", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return model in result.stdout
        except Exception:
            return False

    async def restart(self) -> None:
        """Stop and restart the managed Ollama instance."""
        if self._we_started_it:
            await self.shutdown()
        await self.ensure_running()

    async def shutdown(self) -> None:
        """Stop Ollama only if W.A.D.E. started it."""
        if self._we_started_it and self._process is not None:
            logger.info("[OLLAMA] Shutting down managed instance.")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("[OLLAMA] Process did not terminate cleanly — killing.")
                self._process.kill()
                self._process.wait()
            self._process = None
            self._we_started_it = False

ollama_manager = OllamaManager()