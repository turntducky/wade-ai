from __future__ import annotations

import logging

from dataclasses import dataclass

from app.core.config import ConfigManager

logger = logging.getLogger("wade.model_router")

@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}" if self.provider != "ollama" else self.model

DEFAULT_ROUTING_TABLE: dict[str, str] = {
    "chat":       "qwen2.5:7b",
    "tools":      "qwen2.5:7b",
    "planner":    "qwen2.5:3b",
    "reasoner":   "qwen2.5:14b",
    "code":       "qwen2.5-coder:7b",
    "fast":       "qwen2.5:3b",
    "vision":     "llava:7b",
    "embeddings": "nomic-embed-text",
}

class ModelRouter:
    """Routes a role string to a (provider, model) pair."""
    def __init__(self, routing_table: dict[str, str], default_provider: str = "ollama") -> None:
        self._table = routing_table
        self._default_provider = default_provider

    def resolve(self, role: str) -> ModelRoute:
        """Given a role (e.g. "reasoner"), return a ModelRoute."""
        raw_model = (
            self._table.get(role)
            or self._table.get("fast", "qwen2.5:3b")
        )
        
        if role not in self._table:
            logger.warning(
                "[ROUTER] Role '%s' not in config — using '%s'.",
                role, raw_model,
            )

        if "/" in raw_model:
            provider, model = raw_model.split("/", 1)
            return ModelRoute(provider=provider, model=model)
        
        return ModelRoute(provider=self._default_provider, model=raw_model)

    @classmethod
    def from_config(cls) -> "ModelRouter":
        """Build a router from user config, filling gaps with safe defaults."""
        config = ConfigManager.get()
        table = {**DEFAULT_ROUTING_TABLE, **config.get("models", {})}
        
        llm_cfg = config.get("llm", {})
        default_provider = llm_cfg.get("provider", "ollama")
        
        return cls(table, default_provider=default_provider)

model_router = ModelRouter.from_config()