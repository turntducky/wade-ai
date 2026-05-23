from __future__ import annotations

import asyncio
import logging
import chromadb

from typing import cast
from pathlib import Path

from chromadb.api import ClientAPI

from app.services.inference_client import inference_client as _default_client

logger = logging.getLogger("wade.chroma_utils")

WADE_DIR = Path.home() / ".wade"
CHROMA_DB_DIR = str(WADE_DIR / "vector_store")
_OLLAMA_BASE_URL = "http://localhost:11434"

class UniversalEmbeddingFunction(chromadb.EmbeddingFunction):
    """Embedding function backed by Ollama."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "wade_universal_ef"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "UniversalEmbeddingFunction":
        return UniversalEmbeddingFunction()

    def __call__(self, input: chromadb.Documents) -> chromadb.Embeddings:
        logger.info("UniversalEmbeddingFunction called for %d documents.", len(input))
        results: list[list[float]] = []
        for text in input:
            results.append(self._embed_with_ollama(str(text)))
        return cast(chromadb.Embeddings, results)

    def _embed_with_ollama(self, text: str) -> list[float]:
        """Submit embedding to Ollama, routing through the running event loop when possible."""
        try:
            loop = asyncio.get_running_loop()
            future = asyncio.run_coroutine_threadsafe(_default_client.embed(text), loop)
            return future.result(timeout=30)
        except RuntimeError:
            return self._embed_sync_http(text)
        except Exception:
            return []

    def _embed_sync_http(self, text: str) -> list[float]:
        """Blocking urllib fallback — used only when no event loop is running."""
        import json as _json
        import urllib.request
        from app.services.model_router import model_router
        route = model_router.resolve("embeddings")
        payload = _json.dumps({"model": route.model, "input": text}).encode()
        req = urllib.request.Request(
            f"{_OLLAMA_BASE_URL}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return _json.loads(resp.read())["embeddings"][0]
        except Exception:
            return []

_universal_ef: UniversalEmbeddingFunction | None = None
_shared_chroma_client: ClientAPI | None = None

def get_universal_ef() -> UniversalEmbeddingFunction:
    """Return (or lazily create) the shared UniversalEmbeddingFunction."""
    global _universal_ef
    if _universal_ef is None:
        _universal_ef = UniversalEmbeddingFunction()
    return _universal_ef

def get_shared_chroma_client() -> ClientAPI | None:
    """Return (or lazily create) the shared ChromaDB persistent client."""
    global _shared_chroma_client
    if _shared_chroma_client is None:
        try:
            _shared_chroma_client = chromadb.PersistentClient(path=CHROMA_DB_DIR)
        except Exception as e:
            logger.error("Failed to init shared ChromaDB client: %s", e)
            return None
    return _shared_chroma_client