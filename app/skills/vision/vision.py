import os
import io
import re
import mss
import base64
import asyncio
import logging
import requests

from PIL import Image
from pathlib import Path

from app.skills.registry import register_tool

logger = logging.getLogger("wade_agent_runtime")

_MAX_QUESTION_LEN = 500

_INJECTION_RE = re.compile(
    r"(?i)"
    r"ignore\s+(previous|above|all|prior)\s+(instructions?|prompts?|rules?)|"
    r"disregard\s+(all|previous|prior)\s+(instructions?|prompts?)|"
    r"you\s+are\s+now\s+|"
    r"act\s+as\s+(if\s+you\s+are|a\s+)|"
    r"<\|im_start\||<\|im_end\||"
    r"\[INST\]|\[/INST\]|"
    r"###\s*(system|assistant|user|human)\s*[:,]?|"
    r"^(system|assistant|user)\s*:"
)

def _sanitize_vision_question(question: str) -> str:
    """Basic sanitation to prevent prompt injection attacks through the vision tool's question parameter."""
    question = question.strip()[:_MAX_QUESTION_LEN]
    if _INJECTION_RE.search(question):
        logger.warning("Vision tool: prompt injection pattern detected in question — using fallback.")
        return "Describe what you see in this screenshot."
    return question

WORKSPACE_DIR = Path.home() / ".wade" / "workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

def _capture_and_compress(monitor: int) -> str:
    """Takes a screenshot, compresses it, and returns a Base64 string."""
    with mss.mss() as sct:
        if monitor > len(sct.monitors) - 1:
            monitor = 1
            
        monitor_data = sct.monitors[monitor]
        sct_img = sct.grab(monitor_data)
        
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        
        img.thumbnail((1080, 1080), Image.Resampling.LANCZOS)
        
        img.save(WORKSPACE_DIR / "latest_screenshot.jpg", format="JPEG", quality=85)
        
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85)
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

@register_tool("analyze_screen")
async def analyze_screen(question: str, monitor: int = 1) -> str:
    """Captures the screen and passes it to a Vision LLM to answer W.A.D.E.'s question."""
    try:
        safe_question = _sanitize_vision_question(question)

        def _analyze():
            base64_image = _capture_and_compress(monitor)

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                return "ERROR: OPENAI_API_KEY environment variable is not set."

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }

            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"You are W.A.D.E.'s visual cortex. Analyze this screenshot and answer exactly what is asked: {safe_question}"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "high"}}
                        ]
                    }
                ],
                "max_tokens": 500
            }

            response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=20)
            
            if response.status_code == 200:
                result = response.json()["choices"][0]["message"]["content"]
                return f"--- Vision Analysis (Monitor {monitor}) ---\n{result}"
            else:
                return f"Vision API Error: {response.status_code} - {response.text}"

        return await asyncio.to_thread(_analyze)

    except Exception as e:
        return f"Screenshot/Vision Tool Error: {str(e)}"