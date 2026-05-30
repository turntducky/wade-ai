from __future__ import annotations

import asyncio
import json
import logging

from typing import TYPE_CHECKING

from app.memory.episodes import Episode, EpisodeStore, get_episode_store

if TYPE_CHECKING:
    from app.services.inference_client import InferenceClient

logger = logging.getLogger("wade.memory_agent")

_EXTRACTION_SYSTEM = """\
You are a memory extraction assistant. Your task is to identify durable facts \
about the USER from the conversation below — things about their preferences, \
goals, identity, skills, and ongoing projects.

Focus on what the USER said or revealed, not what the assistant said.

Return ONLY a JSON array of short fact strings. Each fact should be a complete, \
standalone sentence starting with "User". Return an empty array [] if there is \
nothing worth remembering.

Examples of good facts:
  "User prefers dark mode interfaces."
  "User is building a local AI assistant in Python."
  "User's preferred name is Alex."
  "User recently started a new job at a startup."
  "User is moving to Austin in March."

Do not extract:
  - Greetings or pleasantries
  - Questions asked by the assistant
  - Temporary task results or tool outputs
  - Anything the assistant said about itself
  - Anything that could change day-to-day

Return JSON only. No explanation. No markdown fences."""

class MemoryAgent:
    """Agent responsible for extracting durable facts from conversations and consolidating memory over time."""
    def __init__(
        self,
        client: "InferenceClient",
        episode_store: EpisodeStore | None = None,
    ) -> None:
        self._client        = client
        self._episode_store = episode_store if episode_store is not None else get_episode_store()

    async def extract(self, text: str, session_id: str = "", user_text: str = "") -> None:
        """Extract durable facts from a conversation turn and record them in the episode store. Called from Executor after each assistant response."""
        try:
            self._episode_store.record(Episode(
                content=text[:2000],
                type="conversation",
                session_id=session_id,
            ))
        except Exception as e:
            logger.debug("[MEMORY_AGENT] Failed to record conversation episode: %s", e)

        extraction_input = ""
        if user_text:
            extraction_input += f"USER: {user_text[:1000]}\n\n"
        extraction_input += f"ASSISTANT: {text[:500]}"

        messages = [
            {"role": "system", "content": _EXTRACTION_SYSTEM},
            {"role": "user",   "content": extraction_input},
        ]
        try:
            raw, _ = await self._client.chat("fast", messages)
        except Exception as e:
            logger.debug("[MEMORY_AGENT] Extraction LLM call failed: %s", e)
            return

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        try:
            facts: list = json.loads(cleaned)
            if not isinstance(facts, list):
                return
        except (json.JSONDecodeError, ValueError):
            logger.debug("[MEMORY_AGENT] Bad JSON from extraction: %s", cleaned[:100])
            return

        try:
            for fact in facts:
                if isinstance(fact, str) and fact.strip():
                    self._episode_store.record(Episode(
                        content=fact.strip(),
                        type="fact_extracted",
                        session_id=session_id,
                        tags=["auto_extracted"],
                    ))
        except Exception as e:
            logger.debug("[MEMORY_AGENT] Failed to record fact episodes: %s", e)
            return
        if facts:
            logger.debug("[MEMORY_AGENT] Extracted %d facts.", len(facts))

    async def prune_old_memories(self, max_age_days: int = 90) -> None:
        """Prune stale episodic memories from the vector store and all SQLite stores."""
        from app.memory.semantic_memory import SemanticMemoryStream
        from app.core.personality import CHROMA_DB_DIR
        from app.core.config import TASKS_DB_PATH
        from app.core.task_store import TaskStore
        from app.core.telemetry import TelemetryStore, TELEMETRY_DB_PATH

        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
            stream = SemanticMemoryStream(client)
            deleted = stream.prune_old_episodes(max_age_days=max_age_days)
            if deleted:
                logger.info("[MEMORY_AGENT] Pruned %d old vector episodes.", deleted)
        except Exception as e:
            logger.warning("[MEMORY_AGENT] Vector pruning failed: %s", e)

        try:
            deleted = await asyncio.to_thread(
                self._episode_store.prune_old, max_age_days
            )
            if deleted:
                logger.info("[MEMORY_AGENT] Pruned %d old SQLite episodes.", deleted)
        except Exception as e:
            logger.warning("[MEMORY_AGENT] Episode SQLite pruning failed: %s", e)

        try:
            deleted = await asyncio.to_thread(
                TaskStore(TASKS_DB_PATH).prune_old, max_age_days
            )
            if deleted:
                logger.info("[MEMORY_AGENT] Pruned %d old tasks.", deleted)
        except Exception as e:
            logger.warning("[MEMORY_AGENT] Task pruning failed: %s", e)

        try:
            deleted = await asyncio.to_thread(
                TelemetryStore(TELEMETRY_DB_PATH).prune_old, max_age_days
            )
            if deleted:
                logger.info("[MEMORY_AGENT] Pruned %d old telemetry rows.", deleted)
        except Exception as e:
            logger.warning("[MEMORY_AGENT] Telemetry pruning failed: %s", e)

    async def consolidate_today(self) -> None:
        """Consolidate today's episodes into durable facts with a nightly summary."""
        from datetime import datetime
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        episodes = self._episode_store.query_temporal(since=today_start)

        if not episodes:
            logger.debug("[MEMORY_AGENT] No episodes to consolidate today.")
            return

        digest_lines = []
        for ep in episodes:
            if ep.type == "fact_extracted":
                digest_lines.append(f"FACT: {ep.content}")
            elif ep.type == "conversation":
                digest_lines.append(f"CONVERSATION: {ep.content[:200]}")

        if not digest_lines:
            return

        digest_text = "\n".join(digest_lines[:100])
        summary_prompt = (
            f"Here are today's key events and facts from {today_start.strftime('%Y-%m-%d')}:\n\n"
            f"{digest_text}\n\n"
            "Write a concise 2-3 sentence summary of the most important things "
            "from today that are worth remembering long-term. Focus on durable facts."
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a memory consolidation assistant. "
                    "Summarise only what is explicitly stated in the provided facts and conversations. "
                    "Do not infer, invent, or add context not present in the input. "
                    "Return plain prose — no bullet points, no headers."
                ),
            },
            {"role": "user", "content": summary_prompt},
        ]
        try:
            summary, _ = await self._client.chat("reasoner", messages)
        except Exception as e:
            logger.warning("[MEMORY_AGENT] Consolidation LLM call failed: %s", e)
            return

        if not summary.strip():
            return

        self._episode_store.record(Episode(
            content=f"Daily summary ({today_start.strftime('%Y-%m-%d')}): {summary.strip()}",
            type="fact_extracted",
            tags=["daily_summary"],
        ))
        await self.prune_old_memories()
        logger.info("[MEMORY_AGENT] Nightly consolidation complete.")