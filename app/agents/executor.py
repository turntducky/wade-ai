from __future__ import annotations

import json
import time
import asyncio
import logging

from datetime import datetime
from typing import AsyncGenerator, Any

from app.core.task_store import Task
from app.agents.critic import ToolTrace
from app.core.location import get_system_location
from app.skills.semantic_router import SkillRouter
from app.core.personality import PersonalityManager
from app.skills.memory.updater import read_core_memory
from app.services.inference_client import InferenceClient
from app.memory.semantic_memory import SemanticMemoryStream
from app.memory.manager import load_recent_memory, load_user_facts
from app.skills.registry import execute_tool, get_dynamic_tools, load_all_skills, get_tool_risk

_personality = PersonalityManager()
_skill_router: SkillRouter | None = None

def _get_skill_router() -> SkillRouter:
    global _skill_router
    if _skill_router is None:
        _skill_router = SkillRouter(_personality.chroma_client)
    return _skill_router

ALWAYS_ON_TOOLS: frozenset[str] = frozenset(["hot_reload_system", "check_wade_services_health"])
MAX_TOOL_POOL: int = 14
WORKSPACE_CAP: int = 10

_intent_classifier = None

def _get_intent_classifier():
    global _intent_classifier
    if _intent_classifier is None:
        from app.services.model_router import ModelRouter
        from app.services.inference_client import InferenceClient
        from app.core.config import ConfigManager
        from app.core.intent_classifier import IntentClassifier
        config = ConfigManager.get()
        roles = config.get("roles", {}).get("mapping", {})
        router = ModelRouter(roles)
        client = InferenceClient(router=router)
        _intent_classifier = IntentClassifier(
            chroma_client=_personality.chroma_client,
            inference_client=client,
        )
    return _intent_classifier

logger = logging.getLogger("wade.executor")

MAX_TOOL_CALLS = 10
CONTEXT_BUDGET_CHARS = 120_000

_ROLE_MAP = {"user": "user", "assistant": "assistant"}

_MEMORY_SEEK_TERMS = frozenset([
    "remember", "earlier", "last time", "we discussed", "previously",
    "before", "what was", "what did", "you said", "the project",
    "last week", "yesterday", "recall", "you mentioned",
])

def _parse_history_to_messages(memory_ctx: str) -> list[dict]:
    """Parse the raw memory context string into a list of messages with roles."""
    if not memory_ctx or "*No previous" in memory_ctx:
        return []
    msgs: list[dict] = []
    for block in memory_ctx.split("\n\n---\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("...[OLDER MEMORY TRUNCATED]..."):
            block = block[len("...[OLDER MEMORY TRUNCATED]..."):].strip()
        if not block.startswith("###"):
            continue
        first_line, _, rest = block.partition("\n")
        role_raw = first_line.lstrip("#").strip().lower()
        role = _ROLE_MAP.get(role_raw)
        if not role:
            continue
        content = rest.strip()
        if content:
            msgs.append({"role": role, "content": content})
    return msgs

def _get_tools_for_task(goal: str, tier_ctx=None) -> tuple[list[dict], str]:
    """4-stage category-aware tool selection pipeline."""
    load_all_skills()
    all_schemas, _ = get_dynamic_tools()

    from app.skills.registry import TOOL_INVENTORY, get_tool_descriptions, get_tools_by_categories

    router = _get_skill_router()
    router.index_tools()
    classifier = _get_intent_classifier()

    # Stage 1: Category pool — all tools whose category matches detected intent
    categories = asyncio.run(classifier.classify(goal))
    pool: set[str] = set(get_tools_by_categories(categories))

    # Workspace cap: if workspace tools exceed WORKSPACE_CAP, trim by semantic similarity
    if "workspace" in categories:
        ws_tools = [
            n for n in pool
            if TOOL_INVENTORY.get(n, {}).get("manifest") and
            TOOL_INVENTORY[n]["manifest"].category == "workspace"
        ]
        if len(ws_tools) > WORKSPACE_CAP:
            top_ws = router.rank_tools_by_relevance(goal, ws_tools)[:WORKSPACE_CAP]
            pool = (pool - set(ws_tools)) | set(top_ws)

    # Stage 2: Always-on tools
    pool.update(t for t in ALWAYS_ON_TOOLS if t in TOOL_INVENTORY)

    # Stage 3: Cross-category semantic fill — top 4 from outside the current pool
    fill = router.get_relevant_tools(goal, n_results=4, exclude=pool)
    pool.update(fill)

    # Stage 4: Hard cap
    combined = list(pool)
    if len(combined) > MAX_TOOL_POOL:
        combined = router.rank_tools_by_relevance(goal, combined)[:MAX_TOOL_POOL]

    # Tier filtering (preserved from original)
    if tier_ctx is not None and tier_ctx.is_restricted:
        allowed = tier_ctx.allowed_tool_categories
        combined = [
            name for name in combined
            if TOOL_INVENTORY.get(name, {}).get("manifest")
            and TOOL_INVENTORY[name]["manifest"].category in allowed
        ]
    if tier_ctx is not None:
        _tier = tier_ctx.tier
        combined = [
            name for name in combined
            if not (
                TOOL_INVENTORY.get(name, {}).get("manifest") and
                TOOL_INVENTORY[name]["manifest"].allowed_tiers and
                _tier not in TOOL_INVENTORY[name]["manifest"].allowed_tiers
            )
        ]

    if not combined:
        return [], ""

    filtered_schemas = [s for s in all_schemas if s["function"]["name"] in set(combined)]

    all_descriptions = {t["name"]: t for t in get_tool_descriptions()}
    tool_lines: list[str] = []
    instruction_blocks: list[str] = []
    for name in combined:
        if name not in all_descriptions:
            continue
        t = all_descriptions[name]
        tool_lines.append(f"- {t['name']}: {t['description']}")
        manifest = TOOL_INVENTORY.get(name, {}).get("manifest")
        if manifest and manifest.instructions:
            instruction_blocks.append(f"### {name}\n{manifest.instructions}")

    if not tool_lines:
        return filtered_schemas, ""

    parts = [
        "<available_tools>",
        "You have the following tools available for this request:",
        "\n".join(tool_lines),
        "",
        "IMPORTANT: You may ONLY call tools listed above. "
        "Do not invent or guess tool names — if a needed tool is not listed, "
        "say so and ask the user to clarify.",
        "</available_tools>",
    ]
    if instruction_blocks:
        parts += [
            "",
            "<tool_instructions>",
            "Behavioral instructions for the tools above — follow these exactly:",
            "",
            "\n\n".join(instruction_blocks),
            "</tool_instructions>",
        ]
    return filtered_schemas, "\n".join(parts)

class ExecutorAgent:
    """Agent responsible for executing a Task by interacting with the InferenceClient, managing tool calls, and maintaining memory context."""
    def __init__(self, client: InferenceClient, tier_ctx=None) -> None:
        self._client = client
        self._tier_ctx = tier_ctx
        self.traces: list[ToolTrace] = []

    async def execute(self, task: Task, session_id: str | None = None, conv_id: str | None = None, sender_facts_dir=None, cancel_event: asyncio.Event | None = None) -> AsyncGenerator[str, None]:
        self.traces = []

        if self._tier_ctx is not None and not self._tier_ctx.is_admin:
            async for chunk in self._execute_tier_isolated(task, session_id, self._tier_ctx, conv_id=conv_id, sender_facts_dir=sender_facts_dir, cancel_event=cancel_event):
                yield chunk
            return
        
        async def _semantic() -> str:
            if not _personality.chroma_client:
                return ""
            goal_lower = task.goal.lower()
            needs_lookup = (
                len(task.goal.split()) >= 12
                or any(t in goal_lower for t in _MEMORY_SEEK_TERMS)
            )
            if not needs_lookup:
                return ""
            try:
                stream = SemanticMemoryStream(_personality.chroma_client)
                return await asyncio.to_thread(stream.retrieve_context, task.goal, 5)
            except Exception as exc:
                logger.warning("[EXECUTOR] Semantic memory retrieval failed: %s", exc)
                return ""

        (
            identity_ctx,
            workspace_ctx,
            core_memory,
            semantic_ctx,
            tools_result,
            loc_result,
        ) = await asyncio.gather(
            asyncio.to_thread(_personality.get_core_identity_context),
            asyncio.to_thread(_personality.get_relevant_workspace_context, task.goal),
            asyncio.to_thread(read_core_memory),
            _semantic(),
            asyncio.to_thread(_get_tools_for_task, task.goal, self._tier_ctx),
            asyncio.to_thread(get_system_location),
        )

        tool_schemas, tool_ctx = tools_result
        location_str, tz_str = loc_result

        reserved_len = (
            len(identity_ctx) + len(workspace_ctx) + len(tool_ctx) +
            len(core_memory) + len(semantic_ctx) + len(task.goal) +
            2000
        )

        history_budget = max(5000, CONTEXT_BUDGET_CHARS - reserved_len)
        memory_ctx = load_recent_memory(max_chars=history_budget, session_id=session_id)
        tools_instructions = _personality.get_tools_instructions() if tool_schemas else ""
        system_parts = [p for p in [identity_ctx, workspace_ctx, tools_instructions, tool_ctx, core_memory, semantic_ctx] if p]
        system_content = "\n\n".join(system_parts)

        try:
            from pytz import timezone
            tz = timezone(tz_str)
            current_time = datetime.now(tz).strftime("%A, %B %d, %Y at %I:%M %p (%Z)")
        except Exception:
            current_time = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p") + f" ({tz_str})"

        system_content += (
            f"\n\n## System Grounding Context\n"
            f"Current Time: {current_time}\n"
            f"Current Location: {location_str}"
        )

        history_messages = _parse_history_to_messages(memory_ctx)

        if (history_messages
                and history_messages[-1]["role"] == "user"
                and history_messages[-1]["content"].strip() == task.goal.strip()):
            history_messages = history_messages[:-1]

        messages: list[dict] = [
            {"role": "system", "content": system_content},
            *history_messages,
            {"role": "user",   "content": task.goal},
        ]

        tool_calls_made = 0
        call_history: list[tuple[str, str]] = []
        call_counts: dict[str, int] = {}

        if not tool_schemas:
            async for chunk in self._client.complete("chat", messages):
                if cancel_event and cancel_event.is_set():
                    yield "\n\n[Interrupted]"
                    return
                yield chunk
            return

        while True:
            if cancel_event and cancel_event.is_set():
                yield "\n\n[Interrupted]"
                return

            text, tool_calls = await self._client.chat("tools", messages, tools=tool_schemas)

            if text and not tool_calls:
                yield text

            if not tool_calls:
                return

            if tool_calls_made >= MAX_TOOL_CALLS:
                logger.warning("[EXECUTOR] MAX_TOOL_CALLS reached — synthesising from gathered results.")
                messages.append({
                    "role": "user",
                    "content": "You have used the maximum number of tool calls. Based on everything gathered so far, provide a complete and direct answer to the original question.",
                })
                async for chunk in self._client.complete("reasoner", messages):
                    if cancel_event and cancel_event.is_set():
                        yield "\n\n[Interrupted]"
                        return
                    yield chunk
                return

            messages.append({"role": "assistant", "content": text or "", "tool_calls": tool_calls})

            for tc in tool_calls:
                if cancel_event and cancel_event.is_set():
                    yield "\n\n[Interrupted]"
                    return

                fn = tc.get("function", {})
                fn_name = fn.get("name", "")
                fn_args = fn.get("arguments", {})

                if isinstance(fn_args, dict):
                    args_json = json.dumps(fn_args, sort_keys=True)
                elif isinstance(fn_args, str):
                    try:
                        args_json = json.dumps(json.loads(fn_args), sort_keys=True)
                    except json.JSONDecodeError:
                        args_json = fn_args
                else:
                    args_json = str(fn_args)

                call_counts[fn_name] = call_counts.get(fn_name, 0) + 1
                if call_counts[fn_name] > 5:
                    logger.warning(f"[EXECUTOR] Structural recursion limit exceeded for {fn_name}")
                    self.traces.append(ToolTrace(
                        tool_name=fn_name, args_summary=args_json[:200], result_summary="recursion_blocked",
                        risk=get_tool_risk(fn_name), exit_status="recursion_blocked", duration_ms=0,
                    ))
                    yield f"\n\n<recursion_blocked name='{fn_name}' />\n\n"
                    messages.append({"role": "tool", "name": fn_name, "content": "ERROR: Recursion limit exceeded. You called this tool too many times in this execution thread."})
                    continue

                if (fn_name, args_json) in call_history:
                    logger.warning(f"[EXECUTOR] Loop detected: {fn_name} called again with same arguments: {args_json}")
                    self.traces.append(ToolTrace(
                        tool_name=fn_name, args_summary=args_json[:200], result_summary="loop_detected",
                        risk=get_tool_risk(fn_name), exit_status="loop_detected", duration_ms=0,
                    ))
                    yield f"\n\n<loop_detected name='{fn_name}' />\n\n"
                    return

                call_history.append((fn_name, args_json))
                yield f"\n\n<tool_exec name='{fn_name}' />\n\n"

                args_parse_error: str | None = None
                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except json.JSONDecodeError:
                        args_parse_error = fn_args
                        fn_args = {}
                if not isinstance(fn_args, dict):
                    args_parse_error = str(fn_args)
                    fn_args = {}

                _t0 = time.monotonic()
                _approved = True

                if args_parse_error is None and get_tool_risk(fn_name) == "high":
                    from app.core import hitl as _hitl
                    _tier_name = self._tier_ctx.tier if self._tier_ctx else "admin"
                    yield f"\n\n<wade_approval_required tool='{fn_name}' uuid='{task.id}'>{args_json}</wade_approval_required>\n\n"
                    _approved = await _hitl.wait_for_decision(task.id, fn_name, args_json, _tier_name)

                if args_parse_error is not None:
                    tool_result = (
                        f"Error: could not parse arguments for '{fn_name}' — "
                        f"received invalid JSON: {args_parse_error[:200]}. "
                        "Please retry with correctly formatted JSON arguments."
                    )
                    _exit_status = "error"
                elif not _approved:
                    tool_result = (
                        f"The user denied authorization for '{fn_name}'. "
                        "Do not retry this action; acknowledge the denial and continue."
                    )
                    _exit_status = "denied"
                else:
                    _fut = asyncio.ensure_future(execute_tool(fn_name, fn_args))
                    
                    wait_tasks: list[asyncio.Task[Any]] = [_fut]
                    if cancel_event:
                        wait_tasks.append(asyncio.ensure_future(cancel_event.wait()))

                    done, _ = await asyncio.wait(wait_tasks, timeout=60.0, return_when=asyncio.FIRST_COMPLETED)
                    
                    if not done:
                        _fut.cancel()
                        try:
                            await _fut
                        except (asyncio.CancelledError, Exception):
                            pass
                        tool_result = f"Error: tool '{fn_name}' timed out after 60s."
                        _exit_status = "timeout"
                    elif cancel_event and cancel_event.is_set():
                        _fut.cancel()
                        try:
                            await _fut
                        except (asyncio.CancelledError, Exception):
                            pass
                        self.traces.append(ToolTrace(
                            tool_name=fn_name,
                            args_summary=args_json[:200],
                            result_summary="Cancelled by user",
                            risk=get_tool_risk(fn_name),
                            exit_status="cancelled",
                            duration_ms=int((time.monotonic() - _t0) * 1000),
                        ))
                        yield "\n\n[Interrupted]"
                        return
                    else:
                        exc = _fut.exception()
                        tool_result = f"Error: {exc}" if exc is not None else _fut.result()
                        _exit_status = "error" if exc is not None else "success"

                self.traces.append(ToolTrace(
                    tool_name=fn_name,
                    args_summary=args_json[:200],
                    result_summary=str(tool_result)[:300],
                    risk=get_tool_risk(fn_name),
                    exit_status=_exit_status,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    was_retried=False,
                ))

                _display = str(tool_result)
                if len(_display) > 500: _display = _display[:500] + "..."
                yield f"\n\n<tool_result name='{fn_name}'>{_display}</tool_result>\n\n"

                messages.append({
                    "role": "tool",
                    "content": str(tool_result)[:4096],
                    "name": fn_name,
                })

            tool_calls_made += 1

    async def _execute_tier_isolated(
        self,
        task: Task,
        session_id: str | None,
        tier_ctx,
        conv_id: str | None = None,
        sender_facts_dir=None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[str, None]:
        """Execute the task in a tier-isolated manner, ensuring no cross-tier content leakage and applying tier-based tool restrictions."""
        from app.core.tier_personality import build_tier_system_prompt
        from app.core.location import get_system_location

        (
            tier_prompt,
            tool_schemas_result,
            loc_result,
        ) = await asyncio.gather(
            asyncio.to_thread(build_tier_system_prompt, task.goal, tier_ctx),
            asyncio.to_thread(_get_tools_for_task, task.goal, tier_ctx),
            asyncio.to_thread(get_system_location),
        )

        tool_schemas, _tool_ctx = tool_schemas_result
        location_str, tz_str = loc_result

        memory_dir = tier_ctx.user_memory_dir(session_id) if session_id else None
        memory_ctx = load_recent_memory(
            max_chars=8000, session_id=session_id, memory_dir=memory_dir, conv_id=conv_id
        )

        facts_dir = sender_facts_dir or memory_dir
        user_facts = load_user_facts(facts_dir) if facts_dir else ""

        try:
            from pytz import timezone
            tz = timezone(tz_str)
            current_time = datetime.now(tz).strftime("%A, %B %d, %Y at %I:%M %p (%Z)")
        except Exception:
            current_time = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p") + f" ({tz_str})"

        system_content = tier_prompt or ""
        if user_facts:
            system_content += f"\n\n{user_facts}"
        system_content += (
            f"\n\n## System Grounding Context\n"
            f"Current Time: {current_time}\n"
            f"Current Location: {location_str}"
        )

        history_messages_t = _parse_history_to_messages(memory_ctx)

        if (history_messages_t
                and history_messages_t[-1]["role"] == "user"
                and history_messages_t[-1]["content"].strip() == task.goal.strip()):
            history_messages_t = history_messages_t[:-1]

        messages: list[dict] = [
            {"role": "system", "content": system_content},
            *history_messages_t,
            {"role": "user",   "content": task.goal},
        ]

        tool_calls_made = 0
        call_history: list[tuple[str, str]] = []
        call_counts: dict[str, int] = {}

        if not tool_schemas:
            async for chunk in self._client.complete("chat", messages):
                if cancel_event and cancel_event.is_set():
                    yield "\n\n[Interrupted]"
                    return
                yield chunk
            return

        while True:
            if cancel_event and cancel_event.is_set():
                yield "\n\n[Interrupted]"
                return

            text, tool_calls = await self._client.chat("tools", messages, tools=tool_schemas)

            if text and not tool_calls:
                yield text

            if not tool_calls:
                return

            if tool_calls_made >= MAX_TOOL_CALLS:
                logger.warning("[EXECUTOR] MAX_TOOL_CALLS reached — synthesising from gathered results.")
                messages.append({
                    "role": "user",
                    "content": "You have used the maximum number of tool calls. Based on everything gathered so far, provide a complete and direct answer to the original question.",
                })
                async for chunk in self._client.complete("reasoner", messages):
                    if cancel_event and cancel_event.is_set():
                        yield "\n\n[Interrupted]"
                        return
                    yield chunk
                return

            messages.append({"role": "assistant", "content": text or "", "tool_calls": tool_calls})

            for tc in tool_calls:
                if cancel_event and cancel_event.is_set():
                    yield "\n\n[Interrupted]"
                    return

                fn = tc.get("function", {})
                fn_name = fn.get("name", "")
                fn_args = fn.get("arguments", {})

                if isinstance(fn_args, dict):
                    args_json = json.dumps(fn_args, sort_keys=True)
                elif isinstance(fn_args, str):
                    try:
                        args_json = json.dumps(json.loads(fn_args), sort_keys=True)
                    except json.JSONDecodeError:
                        args_json = fn_args
                else:
                    args_json = str(fn_args)

                call_counts[fn_name] = call_counts.get(fn_name, 0) + 1
                if call_counts[fn_name] > 5:
                    logger.warning(f"[EXECUTOR] Structural recursion limit exceeded for {fn_name}")
                    self.traces.append(ToolTrace(
                        tool_name=fn_name, args_summary=args_json[:200], result_summary="recursion_blocked",
                        risk=get_tool_risk(fn_name), exit_status="recursion_blocked", duration_ms=0,
                    ))
                    yield f"\n\n<recursion_blocked name='{fn_name}' />\n\n"
                    messages.append({"role": "tool", "name": fn_name, "content": "ERROR: Recursion limit exceeded. You called this tool too many times in this execution thread."})
                    continue

                if (fn_name, args_json) in call_history:
                    logger.warning(
                        "[EXECUTOR] Loop detected: %s called again with same args: %s",
                        fn_name, args_json,
                    )
                    self.traces.append(ToolTrace(
                        tool_name=fn_name, args_summary=args_json[:200], result_summary="loop_detected",
                        risk=get_tool_risk(fn_name), exit_status="loop_detected", duration_ms=0,
                    ))
                    yield f"\n\n<loop_detected name='{fn_name}' />\n\n"
                    messages.append({"role": "assistant", "content": text or ""})
                    messages.append({"role": "tool", "name": fn_name, "content": "ERROR: Loop detected. You just called this tool with the exact same arguments. Stop repeating yourself and try a different approach or output your final answer."})
                    continue

                call_history.append((fn_name, args_json))
                yield f"\n\n<tool_exec name='{fn_name}' />\n\n"

                args_parse_error_t: str | None = None
                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except json.JSONDecodeError:
                        args_parse_error_t = fn_args
                        fn_args = {}
                if not isinstance(fn_args, dict):
                    args_parse_error_t = str(fn_args)
                    fn_args = {}

                _t0 = time.monotonic()
                _approved_t = True

                if args_parse_error_t is None and get_tool_risk(fn_name) == "high":
                    from app.core import hitl as _hitl
                    yield f"\n\n<wade_approval_required tool='{fn_name}' uuid='{task.id}'>{args_json}</wade_approval_required>\n\n"
                    _approved_t = await _hitl.wait_for_decision(task.id, fn_name, args_json, tier_ctx.tier)

                if args_parse_error_t is not None:
                    tool_result = (
                        f"Error: could not parse arguments for '{fn_name}' — "
                        f"received invalid JSON: {args_parse_error_t[:200]}. "
                        "Please retry with correctly formatted JSON arguments."
                    )
                    _exit_status = "error"
                elif not _approved_t:
                    tool_result = (
                        f"The user denied authorization for '{fn_name}'. "
                        "Do not retry this action; acknowledge the denial and continue."
                    )
                    _exit_status = "denied"
                else:
                    _fut = asyncio.ensure_future(execute_tool(fn_name, fn_args))
                    
                    wait_tasks: list[asyncio.Task[Any]] = [_fut]
                    if cancel_event:
                        wait_tasks.append(asyncio.ensure_future(cancel_event.wait()))

                    done, _ = await asyncio.wait(wait_tasks, timeout=60.0, return_when=asyncio.FIRST_COMPLETED)
                    
                    if not done:
                        _fut.cancel()
                        try:
                            await _fut
                        except (asyncio.CancelledError, Exception):
                            pass
                        tool_result = f"Error: tool '{fn_name}' timed out after 60s."
                        _exit_status = "timeout"
                    elif cancel_event and cancel_event.is_set():
                        _fut.cancel()
                        try:
                            await _fut
                        except (asyncio.CancelledError, Exception):
                            pass
                        self.traces.append(ToolTrace(
                            tool_name=fn_name,
                            args_summary=args_json[:200],
                            result_summary="Cancelled by user",
                            risk=get_tool_risk(fn_name),
                            exit_status="cancelled",
                            duration_ms=int((time.monotonic() - _t0) * 1000),
                        ))
                        yield "\n\n[Interrupted]"
                        return
                    else:
                        exc = _fut.exception()
                        tool_result = f"Error: {exc}" if exc is not None else _fut.result()
                        _exit_status = "error" if exc is not None else "success"

                self.traces.append(ToolTrace(
                    tool_name=fn_name,
                    args_summary=args_json[:200],
                    result_summary=str(tool_result)[:300],
                    risk=get_tool_risk(fn_name),
                    exit_status=_exit_status,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                    was_retried=False,
                ))

                _display = str(tool_result)
                if len(_display) > 500: _display = _display[:500] + "..."
                yield f"\n\n<tool_result name='{fn_name}'>{_display}</tool_result>\n\n"

                messages.append({
                    "role": "tool",
                    "content": str(tool_result)[:4096],
                    "name": fn_name,
                })

            tool_calls_made += 1