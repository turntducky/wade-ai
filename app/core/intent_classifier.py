from __future__ import annotations

import json
import asyncio
import logging

from typing import Any

logger = logging.getLogger("wade.intent_classifier")

_ANCHOR_QUERIES: dict[str, list[str]] = {
    "workspace": [
        "Write a Python script to process CSV files",
        "Read the contents of my config file",
        "Run the test suite and show me the output",
    ],
    "web": [
        "Search the web for the latest news on AI regulation",
        "Look up the documentation for FastAPI",
        "What is the current Bitcoin price?",
    ],
    "system": [
        "Check if the WhatsApp bridge is running",
        "What is my GPU temperature right now?",
        "Run a full system health check and diagnostics",
    ],
    "scheduling": [
        "Remind me to review the PR at 3pm today",
        "Set an alarm for tomorrow morning at 8am",
        "Schedule a daily backup task at midnight",
    ],
    "memory": [
        "Remember that I prefer dark mode in all editors",
        "What did I tell you about my project last week?",
        "Store this note so I can find it later",
    ],
    "communication": [
        "Send a WhatsApp message to John saying I will be late",
        "Draft an email to the team about the release",
        "Reply to the last message in the conversation",
    ],
    "research": [
        "Give me a deep analysis of quantum computing trends in 2026",
        "Research the top competitors in the AI assistant market",
        "Summarize the key findings from the latest retrieval-augmented generation papers",
    ],
}


class IntentClassifier:
    """Hybrid fast/slow intent classifier for W.A.D.E. tool routing.

    Fast path: ChromaDB embedding similarity against per-category anchor queries.
    Slow path: LLM classification, fires when confidence is low or boundary is fuzzy.
    Results are merged (union) — slow path never removes a confidently-detected fast category.
    """

    INCLUDE_THRESHOLD: float = 0.35
    GRAY_ZONE_MAX: float = 0.55
    SLOW_PATH_MARGIN: float = 0.08

    def __init__(self, chroma_client: Any, inference_client: Any = None) -> None:
        self._chroma = chroma_client
        self._inference_client = inference_client
        self._collections: dict[str, Any] = {}
        if chroma_client:
            self._ensure_anchor_collections()

    def _ensure_anchor_collections(self) -> None:
        for category, anchors in _ANCHOR_QUERIES.items():
            coll = self._chroma.get_or_create_collection(name=f"wade_intent_{category}")
            try:
                existing = coll.get()
                if not existing.get("ids"):
                    coll.add(
                        ids=[f"{category}_{i}" for i in range(len(anchors))],
                        documents=anchors,
                    )
            except Exception as exc:
                logger.warning("[INTENT] Could not seed anchors for %s: %s", category, exc)
            self._collections[category] = coll

    def _compute_category_scores(self, user_prompt: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for category, coll in self._collections.items():
            try:
                results = coll.query(query_texts=[user_prompt], n_results=1)
                distances = results.get("distances", [[]])[0]
                scores[category] = 1.0 / (1.0 + distances[0]) if distances else 0.0
            except Exception as exc:
                logger.warning("[INTENT] Score error for %s: %s", category, exc)
                scores[category] = 0.0
        return scores

    def _should_trigger_slow_path(self, scores: dict[str, float]) -> bool:
        """Return True if the slow LLM path should be invoked.

        Triggers when:
        - top score is below GRAY_ZONE_MAX (not confident in any category), OR
        - gap between top two scores is below SLOW_PATH_MARGIN (ambiguous primary intent).
        """
        if not scores:
            return False
        sorted_scores = sorted(scores.values(), reverse=True)
        top_score = sorted_scores[0]
        if top_score < self.GRAY_ZONE_MAX:
            return True
        if len(sorted_scores) >= 2:
            if sorted_scores[0] - sorted_scores[1] < self.SLOW_PATH_MARGIN:
                return True
        return False

    async def _slow_path(self, user_prompt: str) -> list[str]:
        if not self._inference_client:
            return []
        category_list = ", ".join(_ANCHOR_QUERIES.keys())
        messages = [
            {
                "role": "user",
                "content": (
                    f"Classify this user request into one or more of these categories: {category_list}\n\n"
                    f'User request: "{user_prompt}"\n\n'
                    "Respond with ONLY a JSON array of matching category names. "
                    'Example: ["workspace", "web"]. Return only clearly relevant categories.'
                ),
            }
        ]
        try:
            full_text = ""
            async for chunk in self._inference_client.complete("fast", messages):
                full_text += chunk
            data = json.loads(full_text.strip())
            valid_cats = set(_ANCHOR_QUERIES.keys())
            return [cat for cat in data if cat in valid_cats]
        except Exception as exc:
            logger.warning("[INTENT] Slow path failed: %s", exc)
            return []

    async def classify(self, user_prompt: str) -> list[str]:
        """Return a list of detected intent categories for user_prompt."""
        if not self._collections:
            return []

        scores = await asyncio.to_thread(self._compute_category_scores, user_prompt)
        included = [cat for cat, score in scores.items() if score >= self.INCLUDE_THRESHOLD]

        if self._should_trigger_slow_path(scores):
            slow = await self._slow_path(user_prompt)
            for cat in slow:
                if cat not in included:
                    included.append(cat)

        if not included and scores:
            included = [max(scores, key=scores.get)]

        return included
