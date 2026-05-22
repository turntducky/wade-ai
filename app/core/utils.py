import re
import asyncio
import logging
import subprocess

from typing import List, Optional, Union, Tuple

_INTERNAL_TAG_RE = re.compile(
    r"\n*<(?:wade_status|tool_exec|loop_detected)[^>]*/>\n*"
    r"|"
    r"\n*<tool_result[^>]*>.*?</tool_result>\n*"
    r"|"
    r"\n*<(?:critic_note|critic_blocked)>.*?</(?:critic_note|critic_blocked)>\n*",
    re.DOTALL,
)

def strip_internal_tags(text: str) -> str:
    """Remove UI-only orchestrator status tags, leaving only the plain reply text."""
    return _INTERNAL_TAG_RE.sub("", text).strip()

logger = logging.getLogger("wade.utils")

def safe_truncate(text: str, max_chars: int) -> str:
    """Synchronous smart-truncate. Unlike standard truncation which destroys the end of the text (the most recent context), this preserves the Head (system prompts) and Tail (recent conversation)."""
    if max_chars <= 0:
        return ""

    if len(text) <= max_chars:
        return text

    head_budget = int(max_chars * 0.25)
    tail_budget = int(max_chars * 0.50)

    head = text[:head_budget]
    tail = text[-tail_budget:]

    return f"{head}\n\n...[OLDER CONTEXT OMITTED DUE TO LENGTH]...\n\n{tail}"

async def safe_compress(text: str, max_chars: int) -> str:
    """Asynchronous LLM-powered compression. Preserves the Head and Tail, and uses the 'fast' model to summarize the Middle."""
    if max_chars <= 0:
        return ""

    if len(text) <= max_chars:
        return text

    head_budget = int(max_chars * 0.25)
    tail_budget = int(max_chars * 0.50)
    
    head = text[:head_budget]
    tail = text[-tail_budget:]
    middle_to_compress = text[head_budget:-tail_budget]

    if len(middle_to_compress) > 15000:
        middle_to_compress = middle_to_compress[-15000:]

    try:
        from app.services.inference_client import InferenceClient

        client = InferenceClient()

        prompt = (
            f"You are a memory compressor. Summarize this older context in a few sentences. "
            f"Focus ONLY on facts, completed actions, or stated goals. No conversational filler.\n\n"
            f"CONTEXT TO SUMMARIZE:\n{middle_to_compress}"
        )

        logger.info(f"[UTILS] Context overflow! Compressing {len(middle_to_compress)} chars...")

        response = await client.chat(
            model_role="fast", 
            messages=[{"role": "user", "content": prompt}]
        )
        
        summary = response[0] if isinstance(response, tuple) else response
        
        if summary and isinstance(summary, str):
            return f"{head}\n\n...[COMPRESSED HISTORY: {summary.strip()}]...\n\n{tail}"
            
    except Exception as e:
        logger.warning(f"[UTILS] LLM Compression failed ({e}). Falling back to smart-truncate.")

    return f"{head}\n\n...[OLDER CONTEXT OMITTED DUE TO LENGTH]...\n\n{tail}"

def run_command(cmd: Union[str, List[str]], timeout: int = 10, shell: bool = False) -> Tuple[Optional[str], Optional[str], int]:
    """Synchronously executes a system command and returns its (stdout, stderr, returncode)."""
    try:
        if shell and isinstance(cmd, list):
            cmd = " ".join(cmd)
            
        result = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="ignore"
        )
        return result.stdout.strip() if result.stdout else None, \
               result.stderr.strip() if result.stderr else None, \
               result.returncode
    except subprocess.TimeoutExpired:
        return None, "Command timed out", -1
    except Exception as e:
        return None, str(e), -2

async def run_command_async(cmd: Union[str, List[str]], timeout: int = 10, shell: bool = False) -> Tuple[Optional[str], Optional[str], int]:
    """Asynchronous wrapper for run_command, executed in a thread pool to avoid blocking the event loop."""
    return await asyncio.to_thread(run_command, cmd, timeout, shell)