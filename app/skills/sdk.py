from __future__ import annotations

import inspect

from typing import Any, Callable, Awaitable

from app.skills.registry import SkillManifest, register_tool

_VALID_RISK_LEVELS = frozenset({"low", "medium", "high"})

class SkillValidationError(ValueError):
    """Raised at decoration time when a @wade_tool definition violates the schema."""

def wade_tool(*, name: str, description: str, risk: str, parameters: dict[str, Any] | None = None, required_params: list[str] | None = None, category: str = "general",
    allowed_tiers: list[str] | None = None, requires_network: bool = False, cacheable: bool = False, cache_ttl: int = 60, reversible: bool = True, instructions: str = "") -> Callable:
    """Decorator for defining and registering a W.A.D.E. tool inline.

    Enforces SkillManifest schema strictly at decoration time — missing or
    invalid values for the required fields (name, description, risk) raise
    SkillValidationError immediately rather than silently degrading.

    Args:
        name:            Tool name used in LLM function calls. Must be unique.
        description:     One-sentence description surfaced to the LLM.
        risk:            "low" | "medium" | "high" — drives HITL gating.
        parameters:      OpenAI-style `properties` dict describing each param.
        required_params: List of parameter names that are required.
        category:        Skill category for tier-permission grouping.
        allowed_tiers:   Explicit tier allowlist; None / [] means all tiers.
        requires_network: Set True for tools that need internet access.
        cacheable:       True for idempotent tools with stable outputs.
        cache_ttl:       Cache lifetime in seconds when cacheable is True.
        reversible:      False for destructive/irreversible actions.
        instructions:    Extended guidance injected into the LLM context.
    """
    if not isinstance(name, str) or not name.strip():
        raise SkillValidationError(
            "@wade_tool: `name` must be a non-empty string"
        )
    if not isinstance(description, str) or not description.strip():
        raise SkillValidationError(
            f"@wade_tool '{name}': `description` must be a non-empty string"
        )
    if risk not in _VALID_RISK_LEVELS:
        raise SkillValidationError(
            f"@wade_tool '{name}': `risk` must be one of "
            f"{sorted(_VALID_RISK_LEVELS)}, got {risk!r}"
        )

    def decorator(func: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        if not inspect.iscoroutinefunction(func):
            raise SkillValidationError(
                f"@wade_tool '{name}': '{func.__name__}' must be defined with `async def`"
            )

        schema: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": dict(parameters or {}),
                    "required": list(required_params or []),
                },
            },
        }

        manifest = SkillManifest(
            requires_network=requires_network,
            category=category,
            reversible=reversible,
            instructions=instructions,
            cacheable=cacheable,
            cache_ttl=cache_ttl,
            risk=risk,
            allowed_tiers=list(allowed_tiers) if allowed_tiers is not None else [],
        )

        register_tool(schema, manifest=manifest)(func)

        return func

    return decorator