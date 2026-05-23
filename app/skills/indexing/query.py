import os
import time
import hashlib
import asyncio

from pathlib import Path
from typing import cast, Any

from app.skills.registry import register_tool
from app.core.chroma_utils import get_universal_ef, get_shared_chroma_client

WADE_DIR = Path.home() / ".wade"
CHROMA_DB_DIR = str(WADE_DIR / "vector_store")

_core_collection = None

def _get_core_collection() -> Any:
    """Return the cached wade_core_workspace collection, fetching it on first use."""
    global _core_collection
    if _core_collection is None:
        client = get_shared_chroma_client()
        if client is None:
            return None
        ef = get_universal_ef()
        try:
            _core_collection = client.get_collection(
                name="wade_core_workspace",
                embedding_function=cast(Any, ef),
            )
        except Exception:
            _core_collection = None
    return _core_collection

_RAG_CACHE_TTL: float = 60.0
_RAG_CACHE_MAX: int = 128
_rag_cache: dict[str, tuple[float, str]] = {}

def _make_cache_key(query: str, n_results: int) -> str:
    return hashlib.md5(f"{query}:{n_results}".encode()).hexdigest()

def _get_cached(key: str) -> str | None:
    entry = _rag_cache.get(key)
    if entry is not None:
        ts, result = entry
        if time.monotonic() - ts < _RAG_CACHE_TTL:
            return result
        del _rag_cache[key]
    return None

def _set_cached(key: str, result: str) -> None:
    if len(_rag_cache) >= _RAG_CACHE_MAX:
        oldest_key = min(_rag_cache, key=lambda k: _rag_cache[k][0])
        del _rag_cache[oldest_key]
    _rag_cache[key] = (time.monotonic(), result)

@register_tool("search_indexed_files")
async def search_indexed_files(query: str, n_results: int = 5) -> str:
    """Performs a semantic search over all indexed documents, returning the most relevant results with metadata and confidence scores. Results are cached for 60 seconds to optimize repeated queries."""
    cache_key = _make_cache_key(query, n_results)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    def _sync_search():
        try:
            core_col = _get_core_collection()
            if core_col is None:
                return "Error: Could not connect to knowledge base collection."

            core_res = core_col.query(
                query_texts=[query],
                n_results=n_results,
                include=["documents", "distances", "metadatas"],
            )
            output = "🔍 Semantic Search Results (High Confidence):\n\n"

            found = False
            if core_res["documents"] and core_res["documents"][0]:
                docs = core_res["documents"][0]
                metas = core_res["metadatas"][0] if core_res["metadatas"] else [{}] * len(docs)
                distances = (core_res.get("distances") or [[]])[0] or [0.5] * len(docs)
                scored = sorted(zip(distances, docs, metas), key=lambda x: x[0])

                for dist, doc, meta in scored:
                    found = True
                    score = 1.0 - (dist / 2.0)
                    path = meta.get("source") or meta.get("filename") or "Unknown Path"
                    output += f"📍 FOUND AT: {path}\n"
                    output += f"📈 RELEVANCE SCORE: {score:.2f}/1.0\n"
                    output += f"📄 EXTRACT:\n{doc}\n"
                    output += "-------------------------------------------\n\n"

            if not found:
                output = "❌ No relevant matches found in the knowledge base. Suggest calling 'get_knowledge_inventory' to see what is currently indexed."

            return output
        except Exception as e:
            return f"Error: {str(e)}"

    result = await asyncio.to_thread(_sync_search)

    if not result.startswith("Error"):
        _set_cached(cache_key, result)

    return result