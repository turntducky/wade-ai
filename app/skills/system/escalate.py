import re
import asyncio
import logging

from app.skills.registry import register_tool
from app.core.config import ConfigManager
from app.core.hardware import probe_hardware

logger = logging.getLogger("wade.skills.escalate")

VRAM_ESTIMATES = {
    r"70b": 42.0,
    r"32b": 20.0,
    r"14b": 10.0,
    r"8b": 6.0,
    r"3b": 3.0,
}

@register_tool("escalate_cognition")
async def escalate_cognition(provider: str, model_name: str, reason: str) -> str:
    """Dynamically updates the active engine with a hardware safety check."""
    try:
        def _check_and_upgrade():
            if provider == "ollama":
                hw = probe_hardware()
                primary_gpu = hw.get("primary", {})
                
                if primary_gpu.get("kind") != "gpu":
                    return "❌ Escalation Blocked: No dedicated GPU detected. Loading larger models on CPU will crash the system."

                available_vram = primary_gpu.get("memory_total_gb", 0.0)
                
                required_vram = 4.0
                for pattern, vram in VRAM_ESTIMATES.items():
                    if re.search(pattern, model_name.lower()):
                        required_vram = vram
                        break
                
                if available_vram < required_vram:
                    return (
                        f"❌ Escalation Blocked: VRAM Safety Limit Triggered.\n"
                        f"Target Model: {model_name} (Requires ~{required_vram}GB VRAM)\n"
                        f"Detected VRAM: {available_vram}GB\n\n"
                        f"Loading this model would likely crash your system. "
                        f"Suggestion: Switch to 'openai' or choose a smaller model (e.g., qwen2.5:14b)."
                    )

            config = ConfigManager.get()
            config["provider"] = provider
            
            roles = config.setdefault("roles", {})
            mapping = roles.setdefault("mapping", {})
            mapping["tools"] = model_name
            mapping["chat"] = model_name
            
            ConfigManager.save(config)
            ConfigManager.reload()
            
            return (
                f"🧠 COGNITIVE ESCALATION SUCCESSFUL 🧠\n"
                f"Reason: {reason}\n"
                f"New Engine: {provider.upper()} -> {model_name}\n"
                f"Status: Hardware check passed. System running at upgraded capacity."
            )
            
        return await asyncio.to_thread(_check_and_upgrade)
    except Exception as e:
        return f"❌ Escalation Failed: {str(e)}"