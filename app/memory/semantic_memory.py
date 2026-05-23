import time
import uuid
import logging

from typing import cast
from datetime import datetime

try:
    import chromadb
    from chromadb.api import ClientAPI
except ImportError:
    chromadb = None  # type: ignore[assignment]
    ClientAPI = object  # type: ignore[assignment, misc]

from app.core.chroma_utils import get_universal_ef

logger = logging.getLogger("wade.semantic_memory")

class SemanticMemoryStream:
    """Manages episodic memories using ChromaDB for vector storage and retrieval."""
    def __init__(self, chroma_client: ClientAPI):
        self.collection = chroma_client.get_or_create_collection(
            name="wade_episodic_memory",
            embedding_function=get_universal_ef()  # type: ignore[arg-type]
        )

    def store_episode(self, role: str, text: str, session_id: str = "default"):
        """Saves a single message into the semantic memory stream."""
        if not text or len(text.strip()) < 5:
            return

        timestamp = float(time.time())
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        document = f"[{date_str}] {role.upper()}: {text}"

        metadata = {
            "role": str(role),
            "session_id": str(session_id),
            "timestamp": timestamp,
            "date": str(date_str),
            "source": "conversation",
        }

        try:
            self.collection.add(
                documents=[document],
                metadatas=[metadata],
                ids=[memory_id]
            )
        except Exception as e:
            logger.warning("Failed to store episodic memory: %s", e)

    def retrieve_context(self, current_prompt: str, top_k: int = 5,
                         time_decay: bool = True, min_score: float = 0.35) -> str:
        """Retrieves past memories, applying an exponential time-decay penalty to older memories."""
        try:
            results = self.collection.query(
                query_texts=[current_prompt],
                n_results=min(top_k + 3, 8),
                include=["documents", "distances", "metadatas"]
            )

            docs_list = results.get("documents")
            dist_list = results.get("distances")
            meta_list = results.get("metadatas")

            if not docs_list or not docs_list[0]: return ""
            if not dist_list or not dist_list[0]: return ""

            docs = docs_list[0]
            distances = dist_list[0]
            metas = meta_list[0] if meta_list else [{}] * len(docs)

            current_time = float(time.time())
            filtered = []

            for doc, dist, meta in zip(docs, distances, metas):
                base_score = 1.0 - (dist / 2.0)

                final_score = base_score
                if time_decay and "timestamp" in meta:
                    timestamp_val = meta["timestamp"]
                    if isinstance(timestamp_val, (int, float, str)):
                        try:
                            age_days = (current_time - float(timestamp_val)) / 86400.0
                            if age_days > 0:
                                decay_factor = 0.99 ** age_days
                                final_score = base_score * decay_factor
                        except ValueError:
                            pass

                if final_score >= min_score:
                    filtered.append((final_score, doc, meta.get("date", "Unknown Date")))

            filtered.sort(key=lambda x: x[0], reverse=True)
            context_blocks = [doc for _, doc, _ in filtered[:top_k]]

            if context_blocks:
                return "[RECALLED PAST CONVERSATIONS]\n" + "\n".join(context_blocks) + "\n"

        except Exception as e:
            logger.warning("Episodic memory retrieval failed: %s", e)

        return ""

    def delete_by_ids(self, ids: list[str]) -> int:
        """Delete specific episodes by ChromaDB ID. Returns count deleted."""
        if not ids:
            return 0
        try:
            self.collection.delete(ids=ids)
            return len(ids)
        except Exception as e:
            logger.warning("Failed to delete episodes by ids: %s", e)
            return 0

    def forget(self, query: str, top_k: int = 5) -> int:
        """Search for memories semantically matching query and delete them. Returns count deleted."""
        try:
            count = self.collection.count()
            if count == 0:
                return 0
            results = self.collection.query(
                query_texts=[query],
                n_results=min(top_k, count),
                include=[],
            )
            ids_list = results.get("ids")
            if not ids_list or not ids_list[0]:
                return 0
            ids = ids_list[0]
            self.collection.delete(ids=ids)
            return len(ids)
        except Exception as e:
            logger.warning("Forget operation failed: %s", e)
            return 0

    def prune_old_episodes(self, max_age_days: int = 90) -> int:
        """Deletes episodic memories older than the specified number of days, while retaining important types like 'fact_extracted' and 'daily_summary'."""
        cutoff = time.time() - (max_age_days * 86400.0)
        try:
            results = self.collection.get(
                where={"timestamp": {"$lt": cutoff}},
                include=["metadatas"],
            )
            
            ids = results.get("ids") or []
            metas = results.get("metadatas") or []

            keep_types = {"fact_extracted", "daily_summary"}
            ids_to_delete = [
                id_ for id_, meta in zip(ids, metas)
                if meta and meta.get("type") not in keep_types
            ]

            if ids_to_delete:
                self.collection.delete(ids=ids_to_delete)
                logger.info("Pruned %d episodic memories older than %d days.", len(ids_to_delete), max_age_days)
            return len(ids_to_delete)
        except Exception as e:
            logger.warning("Episode pruning failed: %s", e)
            return 0