from __future__ import annotations

import re
import json
import atexit
import logging
import asyncio
import aiohttp

from typing import AsyncGenerator, Awaitable, Callable, Any

from app.core.credentials import CredentialsManager
from app.services.model_router import ModelRouter, ModelRoute, model_router as _default_router

logger = logging.getLogger("wade.inference_client")
OLLAMA_BASE_URL = "http://localhost:11434"

OPENAI_BASE_URL    = "https://api.openai.com/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
GEMINI_BASE_URL    = "https://generativelanguage.googleapis.com/v1beta"

_ROLE_OPTIONS: dict[str, dict] = {
    "tools":    {"temperature": 0.0, "top_p": 0.9, "repeat_penalty": 1.1,  "num_ctx": 32768, "num_predict": 256},
    "planner":  {"temperature": 0.0, "top_p": 0.9, "repeat_penalty": 1.0,  "num_ctx": 16384, "num_predict": 768},
    "code":     {"temperature": 0.1, "top_p": 0.9, "repeat_penalty": 1.1,  "num_ctx": 32768, "num_predict": 4096},
    "reasoner": {"temperature": 0.2, "top_p": 0.9, "repeat_penalty": 1.05, "num_ctx": 32768, "num_predict": 2048},
    "chat":     {"temperature": 0.5, "top_p": 0.95,"repeat_penalty": 1.05, "num_ctx": 16384, "num_predict": 1536},
    "fast":     {"temperature": 0.3, "top_p": 0.9, "repeat_penalty": 1.05, "num_ctx": 8192,  "num_predict": 256},
}
_DEFAULT_OPTIONS: dict = {"temperature": 0.3, "top_p": 0.9, "repeat_penalty": 1.05, "num_ctx": 16384, "num_predict": 1024}

_metrics_hook: Callable[..., Awaitable[None]] | None = None

def set_metrics_hook(fn: Callable[..., Awaitable[None]]) -> None:
    global _metrics_hook
    _metrics_hook = fn

_SESSION: aiohttp.ClientSession | None = None

def _get_session() -> aiohttp.ClientSession:
    """Return the shared ClientSession, creating it if necessary."""
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        connector = aiohttp.TCPConnector(
            limit=10,
            keepalive_timeout=60,
        )
        _SESSION = aiohttp.ClientSession(connector=connector)
    return _SESSION

async def close_session() -> None:
    """Gracefully close the shared session. Call from app lifespan shutdown."""
    global _SESSION
    if _SESSION and not _SESSION.closed:
        await _SESSION.close()
        _SESSION = None

def _close_session_at_exit() -> None:
    """Fallback cleanup for abnormal exits where lifespan shutdown didn't run."""
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        return
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if loop.is_closed() or loop.is_running():
        return
    try:
        loop.run_until_complete(_SESSION.close())
    except Exception:
        pass
    finally:
        _SESSION = None

atexit.register(_close_session_at_exit)

class InferenceClient:
    def __init__(self, router: ModelRouter | None = None) -> None:
        self._router = router or _default_router

    async def is_available(self) -> bool:
        """Return True if the default provider is reachable."""
        route = self._router.resolve("fast")
        if route.provider != "ollama":
            creds = CredentialsManager.get(route.provider)
            return bool(creds and (creds.get("api_key") or creds.get("key")))

        try:
            session = _get_session()
            async with session.get(
                f"{OLLAMA_BASE_URL}/api/version",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def complete(self, model_role: str, messages: list[dict], tools: list[dict] | None = None) -> AsyncGenerator[str, None]:
        """Streaming chat completion dispatch."""
        route = self._router.resolve(model_role)
        
        if route.provider == "openai":
            async for chunk in self._complete_openai(route.model, messages, tools):
                yield chunk
        elif route.provider == "anthropic":
            async for chunk in self._complete_anthropic(route.model, messages, tools):
                yield chunk
        elif route.provider == "gemini":
            async for chunk in self._complete_gemini(route.model, messages, tools):
                yield chunk
        else:
            async for chunk in self._complete_ollama(route.model, messages, tools, model_role):
                yield chunk

    async def chat(self, model_role: str, messages: list[dict], tools: list[dict] | None = None, *, json_format: bool = False) -> tuple[str, list]:
        """Non-streaming chat completion dispatch."""
        route = self._router.resolve(model_role)

        if route.provider == "openai":
            return await self._chat_openai(route.model, messages, tools)
        elif route.provider == "anthropic":
            return await self._chat_anthropic(route.model, messages, tools)
        elif route.provider == "gemini":
            return await self._chat_gemini(route.model, messages, tools)
        else:
            return await self._chat_ollama(route.model, messages, tools, model_role, json_format=json_format)

    async def embed(self, text: str) -> list[float]:
        """Return a vector embedding dispatch."""
        route = self._router.resolve("embeddings")
        
        if route.provider == "openai":
            return await self._embed_openai(route.model, text)
        elif route.provider == "gemini":
            return await self._embed_gemini(route.model, text)
        else:
            return await self._embed_ollama(route.model, text)

    async def _complete_ollama(self, model: str, messages: list[dict], tools: list[dict] | None, model_role: str) -> AsyncGenerator[str, None]:
        options = _ROLE_OPTIONS.get(model_role, _DEFAULT_OPTIONS)
        payload: dict = {"model": model, "messages": messages, "stream": True, "options": options, "keep_alive": -1}
        if tools:
            payload["tools"] = tools

        SUPPRESSION_TAGS = ["<tool_call>", "</tool_call>", "<xml_tags>", "</xml_tags>", "<tool>", "</tool>"]
        buffer = ""

        session = _get_session()
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=300),
        ) as resp:
            if resp.status == 404:
                raise RuntimeError(
                    f"Model '{model}' not found in Ollama (role: {model_role}). "
                    f"Run: ollama pull {model}"
                )
            resp.raise_for_status()
            async for raw_line in resp.content:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("done") and _metrics_hook:
                    try:
                        _pt  = data.get("prompt_eval_count", 0) or 0
                        _ct  = data.get("eval_count", 0) or 0
                        _lat = (data.get("eval_duration", 0) or 0) // 1_000_000
                        await _metrics_hook(model_role, model, _pt, _ct, _lat)
                    except Exception as _hook_err:
                        logger.warning("metrics hook failed: %s", _hook_err)

                content = data.get("message", {}).get("content", "")
                if not content:
                    continue

                buffer += content

                found_tag = False
                for tag in SUPPRESSION_TAGS:
                    if tag in buffer:
                        buffer = buffer.replace(tag, "")
                        found_tag = True
                        logger.warning(
                            "[INFERENCE] Suppressed tool-call tag '%s' in streaming response.",
                            tag,
                        )

                possible_start = any(tag.startswith(buffer[buffer.rfind("<"):]) for tag in SUPPRESSION_TAGS if "<" in buffer)

                if not possible_start or found_tag:
                    if buffer:
                        yield buffer
                        buffer = ""

            if buffer:
                for tag in SUPPRESSION_TAGS:
                    buffer = buffer.replace(tag, "")
                if buffer:
                    yield buffer

    async def _chat_ollama(self, model: str, messages: list[dict], tools: list[dict] | None, model_role: str, *, json_format: bool = False) -> tuple[str, list]:
        options = _ROLE_OPTIONS.get(model_role, _DEFAULT_OPTIONS)
        payload: dict = {"model": model, "messages": messages, "stream": False, "options": options, "keep_alive": -1}
        if tools:
            payload["tools"] = tools
        if json_format:
            payload["format"] = "json"

        session = _get_session()
        try:
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except asyncio.TimeoutError:
            logger.error(f"[INFERENCE] Ollama timed out after 600 seconds for {model_role} / {model}.")
            return '{"status": "error", "verdict": "fail", "detail": "Ollama connection timeout."}', []
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                raise RuntimeError(
                    f"Model '{model}' not found in Ollama (role: {model_role}). "
                    f"Run: ollama pull {model}"
                ) from e
            logger.error(f"[INFERENCE] Ollama request failed ({e.status}): {e}")
            raise
        except Exception as e:
            logger.error(f"[INFERENCE] Ollama connection failed: {e}")
            raise

        if _metrics_hook:
            try:
                _pt  = data.get("prompt_eval_count", 0) or 0
                _ct  = data.get("eval_count", 0) or 0
                _lat = (data.get("eval_duration", 0) or 0) // 1_000_000
                await _metrics_hook(model_role, model, _pt, _ct, _lat)
            except Exception as _hook_err:
                logger.warning("metrics hook failed: %s", _hook_err)

        message = data.get("message", {})
        text = message.get("content", "")
        tool_calls = message.get("tool_calls", [])

        pattern = r'<(tool_call|xml_tags|tool)[^>]*>\s*(\{.*?\})\s*</\1>'
        matches = list(re.finditer(pattern, text, flags=re.DOTALL))
        
        if not matches:
            fallback_pattern = r'(?:<[^>]+>)?\s*(\{.*?\})\s*</(?:tool_call|xml_tags|tool)>'
            matches = list(re.finditer(fallback_pattern, text, flags=re.DOTALL))

        for match in matches:
            try:
                raw_json = match.group(2) if len(match.groups()) > 1 else match.group(1)
                tc_data = json.loads(raw_json)
                if "name" in tc_data:
                    tool_calls.append({
                        "function": {
                            "name": tc_data["name"],
                            "arguments": tc_data.get("arguments", {})
                        }
                    })
            except json.JSONDecodeError:
                continue
        
        if matches:
            text = re.sub(pattern, '', text, flags=re.DOTALL)
            text = re.sub(r'(?:<[^>]+>)?\s*\{.*?\}\s*</(?:tool_call|xml_tags|tool)>', '', text, flags=re.DOTALL)
            text = text.strip()

        return text, tool_calls

    async def _embed_ollama(self, model: str, text: str) -> list[float]:
        session = _get_session()
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": model, "input": text},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["embeddings"][0]

    async def _chat_openai(self, model: str, messages: list[dict], tools: list[dict] | None) -> tuple[str, list]:
        creds = CredentialsManager.get("openai")
        api_key = (creds.get("api_key") or creds.get("key")) if creds else None
        if not api_key:
            raise RuntimeError("OpenAI API key missing. Set it via 'wade config --openai-key KEY'.")

        payload = {"model": model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools

        session = _get_session()
        async with session.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            
        choice = data["choices"][0]
        text = choice["message"].get("content", "")
        tool_calls = choice["message"].get("tool_calls", [])
        return text, tool_calls

    async def _complete_openai(self, model: str, messages: list[dict], tools: list[dict] | None) -> AsyncGenerator[str, None]:
        creds = CredentialsManager.get("openai")
        api_key = (creds.get("api_key") or creds.get("key")) if creds else None
        if not api_key:
             raise RuntimeError("OpenAI API key missing.")

        payload = {"model": model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools

        session = _get_session()
        async with session.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            resp.raise_for_status()
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                if line == "data: [DONE]":
                    break
                
                try:
                    data = json.loads(line[6:])
                    delta = data["choices"][0]["delta"]
                    if "content" in delta:
                        yield delta["content"]
                except Exception:
                    continue

    async def _embed_openai(self, model: str, text: str) -> list[float]:
        creds = CredentialsManager.get("openai")
        api_key = (creds.get("api_key") or creds.get("key")) if creds else None
        session = _get_session()
        async with session.post(
            f"{OPENAI_BASE_URL}/embeddings",
            json={"model": model, "input": text},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["data"][0]["embedding"]

    async def _chat_anthropic(self, model: str, messages: list[dict], tools: list[dict] | None) -> tuple[str, list]:
        creds = CredentialsManager.get("anthropic")
        api_key = (creds.get("api_key") or creds.get("key")) if creds else None
        if not api_key:
            raise RuntimeError("Anthropic API key missing.")

        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        anthropic_messages = [m for m in messages if m["role"] != "system"]

        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": anthropic_messages,
            "system": system_msg,
            "stream": False
        }

        session = _get_session()
        async with session.post(
            f"{ANTHROPIC_BASE_URL}/messages",
            json=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            
        text = "".join(c["text"] for c in data["content"] if c["type"] == "text")
        return text, []

    async def _complete_anthropic(self, model: str, messages: list[dict], tools: list[dict] | None) -> AsyncGenerator[str, None]:
        creds = CredentialsManager.get("anthropic")
        api_key = (creds.get("api_key") or creds.get("key")) if creds else None
        if not api_key:
             raise RuntimeError("Anthropic API key missing.")

        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        anthropic_messages = [m for m in messages if m["role"] != "system"]

        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": anthropic_messages,
            "system": system_msg,
            "stream": True
        }

        session = _get_session()
        async with session.post(
            f"{ANTHROPIC_BASE_URL}/messages",
            json=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            resp.raise_for_status()
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                
                try:
                    data = json.loads(line[6:])
                    if data["type"] == "content_block_delta":
                        yield data["delta"].get("text", "")
                except Exception:
                    continue

    async def _chat_gemini(self, model: str, messages: list[dict], tools: list[dict] | None) -> tuple[str, list]:
        creds = CredentialsManager.get("gemini")
        api_key = (creds.get("api_key") or creds.get("key")) if creds else None
        if not api_key:
            raise RuntimeError("Gemini API key missing.")

        gemini_contents = []
        system_instruction = None
        for m in messages:
            if m["role"] == "system":
                system_instruction = {"parts": [{"text": m["content"]}]}
            else:
                role = "user" if m["role"] == "user" else "model"
                gemini_contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload: dict = {"contents": gemini_contents}
        if system_instruction:
            payload["system_instruction"] = system_instruction

        session = _get_session()
        async with session.post(
            f"{GEMINI_BASE_URL}/models/{model}:generateContent?key={api_key}",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return text, []

    async def _complete_gemini(self, model: str, messages: list[dict], tools: list[dict] | None) -> AsyncGenerator[str, None]:
        creds = CredentialsManager.get("gemini")
        api_key = (creds.get("api_key") or creds.get("key")) if creds else None
        if not api_key:
             raise RuntimeError("Gemini API key missing.")

        gemini_contents = []
        system_instruction = None
        for m in messages:
            if m["role"] == "system":
                system_instruction = {"parts": [{"text": m["content"]}]}
            else:
                role = "user" if m["role"] == "user" else "model"
                gemini_contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload: dict = {"contents": gemini_contents}
        if system_instruction:
            payload["system_instruction"] = system_instruction

        session = _get_session()
        async with session.post(
            f"{GEMINI_BASE_URL}/models/{model}:streamGenerateContent?alt=sse&key={api_key}",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            resp.raise_for_status()
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                
                try:
                    data = json.loads(line[6:])
                    yield data["candidates"][0]["content"]["parts"][0]["text"]
                except Exception:
                    continue

    async def _embed_gemini(self, model: str, text: str) -> list[float]:
        creds = CredentialsManager.get("gemini")
        api_key = (creds.get("api_key") or creds.get("key")) if creds else None
        session = _get_session()
        async with session.post(
            f"{GEMINI_BASE_URL}/models/{model}:embedContent?key={api_key}",
            json={"model": f"models/{model}", "content": {"parts": [{"text": text}]}},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["embedding"]["values"]

inference_client = InferenceClient()