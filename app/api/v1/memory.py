from __future__ import annotations

import base64
import asyncio

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends

from app.core.security import require_admin

router = APIRouter(prefix="/api/memory", tags=["memory"])

def _encode_id(topic_key: str) -> str:
    return base64.urlsafe_b64encode(topic_key.encode()).decode().rstrip("=")

def _decode_id(fact_id: str) -> str:
    padding = 4 - len(fact_id) % 4
    if padding != 4:
        fact_id += "=" * padding
    try:
        return base64.urlsafe_b64decode(fact_id.encode()).decode()
    except Exception:
        raise ValueError(f"Invalid fact_id: {fact_id!r}")

def _load_facts() -> dict:
    from app.skills.memory.updater import _load_memory_db, memory_file_lock
    with memory_file_lock:
        return _load_memory_db()

@router.get("/facts")
async def list_facts(
    limit: int = 100,
    offset: int = 0,
    _: None = Depends(require_admin),
) -> dict:
    data = await asyncio.to_thread(_load_facts)
    items = list(data.items())
    page = items[offset: offset + limit]
    return {
        "total": len(items),
        "facts": [
            {
                "id": _encode_id(k),
                "topic": k,
                "fact": v.get("fact"),
                "timestamp": v.get("timestamp"),
            }
            for k, v in page
        ],
    }

class FactUpdateBody(BaseModel):
    fact: str | list

@router.put("/facts/{fact_id}")
async def update_fact(
    fact_id: str,
    body: FactUpdateBody,
    _: None = Depends(require_admin),
) -> dict:
    try:
        topic_key = _decode_id(fact_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from datetime import datetime
    from app.skills.memory.updater import _load_memory_db, _save_memory_db, memory_file_lock

    def _update():
        with memory_file_lock:
            data = _load_memory_db()
            if topic_key not in data:
                return None
            data[topic_key]["fact"] = body.fact
            data[topic_key]["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _save_memory_db(data)
            return data[topic_key]

    result = await asyncio.to_thread(_update)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Fact '{topic_key}' not found")
    return {"status": "updated", "topic": topic_key, "fact": result["fact"]}

@router.delete("/facts/{fact_id}")
async def delete_fact(
    fact_id: str,
    _: None = Depends(require_admin),
) -> dict:
    try:
        topic_key = _decode_id(fact_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from app.skills.memory.updater import _load_memory_db, _save_memory_db, memory_file_lock

    def _delete():
        with memory_file_lock:
            data = _load_memory_db()
            if topic_key not in data:
                return False
            del data[topic_key]
            _save_memory_db(data)
            return True

    deleted = await asyncio.to_thread(_delete)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Fact '{topic_key}' not found")
    return {"status": "deleted", "topic": topic_key}

class ForgetBody(BaseModel):
    query: str
    top_k: int = 5

@router.post("/forget")
async def forget_context(
    body: ForgetBody,
    _: None = Depends(require_admin),
) -> dict:
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")

    chromadb_deleted = 0
    try:
        from app.core.chroma_utils import get_shared_chroma_client
        from app.memory.semantic_memory import SemanticMemoryStream
        client = get_shared_chroma_client()
        if client is not None:
            stream = SemanticMemoryStream(client)
            chromadb_deleted = await asyncio.to_thread(stream.forget, body.query, body.top_k)
    except Exception:
        pass

    episodes_deleted = 0
    try:
        from app.memory.episodes import get_episode_store
        store = get_episode_store()
        episodes_deleted = await asyncio.to_thread(store.delete_matching, body.query)
    except Exception:
        pass

    return {
        "status": "ok",
        "query": body.query,
        "chromadb_deleted": chromadb_deleted,
        "episodes_deleted": episodes_deleted,
    }