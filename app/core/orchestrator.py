from __future__ import annotations

import socket
import asyncio
import logging

from typing import TYPE_CHECKING, AsyncGenerator, Callable

from app.core.config import TASKS_DB_PATH
from app.core.utils import strip_internal_tags
from app.memory.manager import append_to_memory
from app.core.classifier import classify, _PATH_RE
from app.core.task_store import Task, TaskStatus, TaskStore, _TERMINAL_STATUSES
from app.services.inference_client import InferenceClient, inference_client as _default_client
from app.agents.critic import CriticAgent, GoalAnchor, ToolTrace, CriticVerdict, ACCEPT_THRESHOLD, CRITIC_K

if TYPE_CHECKING:
    from app.core.events import InternalEventBus, WadeEvent
    from app.core.telemetry import TelemetryStore

logger = logging.getLogger("wade.orchestrator")

def _check_connectivity() -> bool:
    """Quick TCP probe to Google DNS. Uses instance-level timeout — does NOT mutate the global socket default."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False

class Orchestrator:
    """Central coordinator for processing tasks: planning, execution, memory management, and approval gating."""
    def __init__(
        self,
        task_store: TaskStore | None = None,
        inference_client: InferenceClient | None = None,
    ) -> None:
        self._store = task_store or TaskStore(TASKS_DB_PATH)
        self._client = inference_client or _default_client
        self._planner = None
        self._executor_cls = None
        self._approval_callbacks: list[Callable] = []
        self._memory_agent = None
        self._critic = CriticAgent(self._client)
        self._telemetry: "TelemetryStore | None" = None
        self._cancel_events: dict[str, asyncio.Event] = {}

    def set_planner(self, planner) -> None:
        self._planner = planner

    def set_executor_cls(self, executor_cls: type) -> None:
        self._executor_cls = executor_cls

    def register_approval_callback(self, cb: Callable) -> None:
        self._approval_callbacks.append(cb)

    def set_memory_agent(self, memory_agent) -> None:
        """Register the MemoryAgent for background passive extraction."""
        self._memory_agent = memory_agent

    def set_telemetry(self, store: "TelemetryStore") -> None:
        self._telemetry = store

    async def _tel_verdict(
        self,
        task_id: str,
        check_type: str,
        verdict,
        step_task_id: str | None = None,
    ) -> None:
        if self._telemetry is None:
            return
        try:
            await asyncio.to_thread(
                self._telemetry.save_verdict,
                task_id,
                check_type,
                verdict.status,
                verdict.confidence,
                verdict.reason,
                verdict.surface_to_user,
                verdict.goal_progress_delta,
                step_task_id,
            )
        except Exception as _err:
            logger.warning("telemetry verdict write failed: %s", _err)

    def list_recent_tasks(self, limit: int = 50):
        """Public accessor for the task store's list_recent."""
        return self._store.list_recent(limit=limit)

    async def process(self, prompt: str, session_id: str | None = None, is_system: bool = False, created_by: str | None = None, tier_ctx=None, conv_id: str | None = None, sender_facts_dir=None) -> AsyncGenerator[str, None]:
        """Main entry point for processing a user/system prompt. Handles task creation, routing, and error handling."""
        import httpx
        if not created_by:
            created_by = "system" if is_system else "user"

        _mem_dir = None
        if tier_ctx is not None and not tier_ctx.is_admin and session_id:
            _mem_dir = tier_ctx.user_memory_dir(session_id)

        if created_by == "user":
            append_to_memory("user", prompt, session_id=session_id, memory_dir=_mem_dir, conv_id=conv_id)

        task = Task(goal=prompt, created_by=created_by)
        self._store.save(task)
        self._store.update_status(task.id, TaskStatus.IN_PROGRESS)

        cancel_event = None
        if session_id:
            cancel_event = asyncio.Event()
            self._cancel_events[session_id] = cancel_event

        max_retries = 3
        last_error: Exception | None = None

        try:
            for attempt in range(1, max_retries + 1):
                collected: list[str] = []
                try:
                    in_thought = False

                    async for chunk in self._execute_task(task, session_id=session_id, tier_ctx=tier_ctx, conv_id=conv_id, sender_facts_dir=sender_facts_dir, cancel_event=cancel_event):
                        if "<think>" in chunk and not in_thought:
                            in_thought = True
                            chunk = chunk.replace("<think>", "\n\n🧠 [Thinking: ")

                        if "</think>" in chunk and in_thought:
                            in_thought = False
                            chunk = chunk.replace("</think>", "]\n\n")

                        collected.append(chunk)
                        yield chunk

                    if in_thought:
                        yield "]\n\n"
                        collected.append("]\n\n")

                    final_text = "".join(collected)
                    current = self._store.get(task.id)
                    if current is None or current.status not in _TERMINAL_STATUSES - {TaskStatus.COMPLETED}:
                        self._store.update_status(task.id, TaskStatus.COMPLETED, result=final_text)

                    if created_by in ("user", "proactive"):
                        append_to_memory("assistant", strip_internal_tags(final_text), session_id=session_id, memory_dir=_mem_dir, conv_id=conv_id)

                    if self._memory_agent and not is_system and final_text:
                        def _log_task_exc(t: asyncio.Task) -> None:
                            if not t.cancelled() and t.exception():
                                logger.warning("[ORCHESTRATOR] Background task raised: %s", t.exception())

                        if tier_ctx is None or tier_ctx.is_admin:
                            _t = asyncio.create_task(
                                self._memory_agent.extract(
                                    final_text,
                                    session_id=session_id or "",
                                    user_text=prompt,
                                )
                            )
                            _t.add_done_callback(_log_task_exc)
                        else:
                            _facts_dir = sender_facts_dir or _mem_dir
                            if _facts_dir is not None:
                                from app.memory.passive_extractor import extract_and_store
                                _t = asyncio.create_task(
                                    extract_and_store(prompt, facts_file=_facts_dir / "facts.json")
                                )
                                _t.add_done_callback(_log_task_exc)
                    return
                except httpx.ConnectError as e:
                    last_error = e
                    logger.warning(
                        "[ORCHESTRATOR] Inference connection lost (attempt %d/%d): %s",
                        attempt, max_retries, e,
                    )
                    if attempt < max_retries:
                        backoff = 2 ** attempt
                        logger.info("[ORCHESTRATOR] Retrying in %ds...", backoff)
                        try:
                            from app.services.ollama_manager import ollama_manager
                            await ollama_manager.restart()
                        except Exception as restart_err:
                            logger.warning("[ORCHESTRATOR] Ollama restart failed: %s", restart_err)
                        await asyncio.sleep(backoff)
                except Exception as e:
                    if cancel_event and cancel_event.is_set():
                        self._store.update_status(task.id, TaskStatus.CANCELLED, result="User cancelled")
                        return

                    logger.error("[ORCHESTRATOR] Task %s failed: %s", task.id, e, exc_info=True)
                    self._store.update_status(task.id, TaskStatus.FAILED, result=str(e))

                    is_non_admin = tier_ctx is not None and not tier_ctx.is_admin
                    if is_non_admin:
                        err_msg = "Sorry, I wasn't able to process that. Please try again."
                    else:
                        err_detail = strip_internal_tags(str(e)).strip()
                        if not err_detail:
                            err_detail = f"{type(e).__name__}"
                        err_msg = f"I ran into an error: {err_detail}"
                    yield f"\n\n{err_msg}"
                    if created_by in ("user", "proactive"):
                        partial = strip_internal_tags("".join(collected)) if collected else ""
                        mem_text = (f"{partial}\n{err_msg}" if partial else err_msg).strip()
                        append_to_memory("assistant", mem_text, session_id=session_id, memory_dir=_mem_dir, conv_id=conv_id)
                    return

            self._store.update_status(task.id, TaskStatus.FAILED, result=str(last_error))
            yield "\n\n[Lost connection to inference engine after 3 retries. Please check Ollama is running.]"
            if created_by in ("user", "proactive"):
                append_to_memory("assistant", "[Connection error — inference engine unavailable]", session_id=session_id, memory_dir=_mem_dir, conv_id=conv_id)
        finally:
            if session_id and session_id in self._cancel_events:
                del self._cancel_events[session_id]

    def cancel_session(self, session_id: str) -> bool:
        """Triggers the cancellation event for the given session ID."""
        if session_id in self._cancel_events:
            self._cancel_events[session_id].set()
            return True
        return False

    async def submit(self, task: Task) -> None:
        """Submit a background task (from a monitor daemon). Non-blocking."""
        self._store.save(task)
        _t = asyncio.create_task(self._run_background(task))
        _t.add_done_callback(
            lambda t: logger.warning("[ORCHESTRATOR] Background task raised: %s", t.exception())
            if not t.cancelled() and t.exception() else None
        )

    def _wave_levels(self, tasks: list[Task]) -> list[list[int]]:
        """Group task indices by dependency depth for parallel wave execution."""
        id_to_idx = {t.id: i for i, t in enumerate(tasks)}
        levels: dict[int, int] = {}

        def depth(i: int) -> int:
            if i in levels:
                return levels[i]
            dep_idxs = [id_to_idx[dep_id] for dep_id in tasks[i].depends_on if dep_id in id_to_idx]
            d = 1 + max((depth(di) for di in dep_idxs), default=-1)
            levels[i] = d
            return d

        for i in range(len(tasks)):
            depth(i)
        max_level = max(levels.values(), default=0)
        return [[i for i, lv in levels.items() if lv == lvl] for lvl in range(max_level + 1)]

    async def _execute_task(self, task: Task, session_id: str | None = None, tier_ctx=None, conv_id: str | None = None, sender_facts_dir=None, cancel_event: asyncio.Event | None = None) -> AsyncGenerator[str, None]:
        """Internal method to execute a task, with optional planning and approval gating. Yields text chunks of the response."""
        executor_cls = self._executor_cls

        if executor_cls is None:
            raise RuntimeError(
                "Executor not configured. Call set_executor_cls() before processing tasks."
            )

        if not task.is_reversible:
            approved = await self._request_approval(task)
            if not approved:
                yield "[Action requires approval. Task cancelled.]"
                self._store.update_status(task.id, TaskStatus.CANCELLED)
                return

        if self._planner is None or not self._planner.needs_planning(task.goal):
            executor = executor_cls(self._client, tier_ctx=tier_ctx)
            async for chunk in executor.execute(task, session_id=session_id, conv_id=conv_id, sender_facts_dir=sender_facts_dir, cancel_event=cancel_event):
                yield chunk
            if self._telemetry:
                _traces = list(executor.traces)
                if _traces:
                    try:
                        await asyncio.to_thread(self._telemetry.save_traces, _traces, task.id)
                    except Exception as _err:
                        logger.warning("telemetry trace write failed: %s", _err)
            return

        tier = classify(task.goal)
        if tier == "medium" and _PATH_RE.search(task.goal):
            tier = "complex"

        yield "\n\n<wade_status type='planning' />\n\n"

        net_ok = await asyncio.to_thread(_check_connectivity)
        self._store.update_status(task.id, TaskStatus.PLANNING)
        anchor, subtasks = await self._planner.decompose(task.goal, network_available=net_ok)
        self._critic.reset_step_verdicts()

        anchor_verdict = await self._critic.validate_anchor_structure(anchor)
        await self._tel_verdict(task.id, "anchor_structure", anchor_verdict)
        if anchor_verdict.status == "blocked" and anchor_verdict.confidence >= ACCEPT_THRESHOLD:
            if tier == "medium":
                tier = "complex"
                try:
                    anchor, subtasks = await self._planner.decompose(task.goal, network_available=net_ok)
                except Exception as _decompose_err:
                    self._store.update_status(task.id, TaskStatus.INVALID_PLAN)
                    yield f"\n\n<wade_status type='blocked'>Re-decomposition failed: {_decompose_err}</wade_status>\n\n"
                    return
                self._critic.reset_step_verdicts()
                anchor_verdict = await self._critic.validate_anchor_structure(anchor)
                await self._tel_verdict(task.id, "anchor_structure", anchor_verdict)
                if anchor_verdict.status == "blocked" and anchor_verdict.confidence >= ACCEPT_THRESHOLD:
                    self._store.update_status(task.id, TaskStatus.INVALID_PLAN)
                    yield f"\n\n<wade_status type='blocked'>{anchor_verdict.reason}</wade_status>\n\n"
                    return
            else:
                self._store.update_status(task.id, TaskStatus.INVALID_PLAN)
                yield f"\n\n<wade_status type='blocked'>{anchor_verdict.reason}</wade_status>\n\n"
                return

        if anchor_verdict.status == "revise" and anchor_verdict.revised_steps:
            parsed_criteria: list[str] = [
                str(step.get("criterion", step.get("description", str(step)))) if isinstance(step, dict) else str(step)
                for step in anchor_verdict.revised_steps
            ]
            
            anchor = GoalAnchor(
                original_goal=anchor.original_goal,
                success_criteria=parsed_criteria,
                constraints=anchor.constraints,
            )

        if len(subtasks) > 1 and tier == "complex":
            plan_verdict = await self._critic.validate_plan(anchor, subtasks)
            await self._tel_verdict(task.id, "plan", plan_verdict)
            if plan_verdict.status == "blocked" and plan_verdict.confidence >= ACCEPT_THRESHOLD:
                self._store.update_status(task.id, TaskStatus.INVALID_PLAN)
                yield f"\n\n<wade_status type='blocked'>{plan_verdict.reason}</wade_status>\n\n"
                return
            if plan_verdict.status == "revise" and plan_verdict.confidence >= ACCEPT_THRESHOLD:
                feedback_goal = (
                    task.goal
                    + f"\n\n[Plan critic feedback — revise before proceeding]: {plan_verdict.reason}"
                )
                _, subtasks = await self._planner.decompose(feedback_goal, network_available=net_ok)
                if not subtasks:
                    subtasks = [Task(goal=task.goal)]

        if len(subtasks) == 1:
            subtasks[0].parent_id = task.id
            self._store.save(subtasks[0])
            executor = executor_cls(self._client, tier_ctx=tier_ctx)
            async for chunk in executor.execute(subtasks[0], session_id=session_id):
                yield chunk
            if self._telemetry:
                _traces = list(executor.traces)
                if _traces:
                    try:
                        await asyncio.to_thread(self._telemetry.save_traces, _traces, subtasks[0].id)
                    except Exception as _err:
                        logger.warning("telemetry trace write failed: %s", _err)
            return

        self._store.update_status(task.id, TaskStatus.IN_PROGRESS)
        for st in subtasks:
            st.parent_id = task.id
            self._store.save(st)

        all_traces: list[ToolTrace] = []
        results: list[str | BaseException | None] = [None] * len(subtasks)
        step_counter = 0

        async def run_subtask(st: Task, idx: int) -> str:
            nonlocal step_counter
            self._store.update_status(st.id, TaskStatus.IN_PROGRESS)
            executor = executor_cls(self._client, tier_ctx=tier_ctx)
            chunks: list[str] = []
            async for chunk in executor.execute(st, session_id=session_id):
                chunks.append(chunk)
            result = "".join(chunks)
            step_traces = list(executor.traces)
            all_traces.extend(step_traces)
            step_counter += 1
            if self._telemetry and step_traces:
                try:
                    await asyncio.to_thread(self._telemetry.save_traces, step_traces, st.id)
                except Exception as _err:
                    logger.warning("telemetry trace write failed: %s", _err)

            step_verdict = await self._critic.verify_step(anchor, st, step_traces)
            await self._tel_verdict(task.id, "step", step_verdict, step_task_id=st.id)

            if step_verdict.status != "ok":
                directly_adjacent = [t for t in subtasks if st.id in t.depends_on]
                if directly_adjacent and step_verdict.status == "revise":
                    step_verdict = CriticVerdict(
                        status="blocked",
                        confidence=step_verdict.confidence,
                        reason=(
                            f"Step '{st.goal}' failed and blocks "
                            f"{len(directly_adjacent)} dependent step(s). {step_verdict.reason}"
                        ),
                        surface_to_user=True,
                    )
                if step_verdict.status == "blocked" and step_verdict.confidence >= ACCEPT_THRESHOLD:
                    self._store.update_status(st.id, TaskStatus.GOAL_NOT_SATISFIED)
                    raise RuntimeError(
                        f"<critic_blocked>{step_verdict.reason}</critic_blocked>"
                    )
                if step_verdict.status == "revise" and step_verdict.surface_to_user:
                    result += f"\n\n<critic_note>{step_verdict.reason}</critic_note>"

            high_risk_in_step = any(t.risk == "high" for t in step_traces)
            if tier == "complex" and (step_counter % CRITIC_K == 0 or high_risk_in_step):
                mid_verdict = await self._critic.quick_anchor_check(anchor, all_traces)
                await self._tel_verdict(task.id, "anchor_check", mid_verdict)
                if mid_verdict.status == "suspect":
                    second = await self._critic.quick_anchor_check(anchor, all_traces)
                    await self._tel_verdict(task.id, "anchor_check_second", second)
                    if second.status == "blocked" and second.confidence >= ACCEPT_THRESHOLD:
                        raise RuntimeError(
                            f"<critic_blocked>Mid-pipeline: {second.reason}</critic_blocked>"
                        )
                elif mid_verdict.status == "blocked" and mid_verdict.confidence >= ACCEPT_THRESHOLD:
                    raise RuntimeError(
                        f"<critic_blocked>Mid-pipeline: {mid_verdict.reason}</critic_blocked>"
                    )

            self._store.update_status(st.id, TaskStatus.COMPLETED, result=result)
            return result

        id_to_idx: dict[str, int] = {st.id: i for i, st in enumerate(subtasks)}

        for wave in self._wave_levels(subtasks):
            for i in wave:
                dep_results = []
                for dep_id in subtasks[i].depends_on:
                    if dep_id not in id_to_idx or results[id_to_idx[dep_id]] is None:
                        continue
                    dep_str = str(results[id_to_idx[dep_id]])
                    if len(dep_str) > 800:
                        dep_str = dep_str[:800] + "…[truncated]"
                    dep_results.append(f"Result from step {id_to_idx[dep_id]+1}: {dep_str}")
                if dep_results:
                    subtasks[i].goal = (
                        subtasks[i].goal
                        + "\n\n[Context from prior steps]\n"
                        + "\n".join(dep_results)
                    )

            wave_raw = await asyncio.gather(
                *[run_subtask(subtasks[i], i) for i in wave], return_exceptions=True
            )
            for i, r in zip(wave, wave_raw):
                results[i] = r

        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            for i, r in enumerate(results):
                if isinstance(r, BaseException):
                    self._store.update_status(subtasks[i].id, TaskStatus.FAILED, result=str(r))
            raise errors[0]

        final_parts = [strip_internal_tags(str(r)) for r in results if r and not isinstance(r, BaseException)]
        final_combined = "\n\n".join(final_parts)

        if tier == "complex":
            sat_verdict = await self._critic.validate_anchor_satisfaction(
                anchor, all_traces, final_combined
            )
            await self._tel_verdict(task.id, "satisfaction", sat_verdict)
            if sat_verdict.status == "blocked" and sat_verdict.confidence >= ACCEPT_THRESHOLD:
                self._store.update_status(task.id, TaskStatus.GOAL_NOT_SATISFIED)
                logger.warning("[ORCHESTRATOR] Goal not fully satisfied: %s", sat_verdict.reason)
            elif sat_verdict.status == "revise":
                logger.warning("[ORCHESTRATOR] Critic revision needed: %s", sat_verdict.reason)
            synthesis_model = (
                "chat"
                if sat_verdict.status == "ok" and sat_verdict.confidence >= ACCEPT_THRESHOLD
                else "reasoner"
            )
            synthesis_prompt = (
                f"Original goal: {task.goal}\n\n"
                + "\n\n".join(f"Step {i+1} result:\n{r}" for i, r in enumerate(final_parts))
                + "\n\nSynthesize the above results into a clear, concise final response."
            )
            messages = [{"role": "user", "content": synthesis_prompt}]
            async for chunk in self._client.complete(synthesis_model, messages):
                yield chunk
        else:
            synthesis_prompt = (
                f"Original goal: {task.goal}\n\n"
                + "\n\n".join(f"Step {i+1} result:\n{r}" for i, r in enumerate(final_parts))
                + "\n\nSynthesize the above results into a clear, concise final response."
            )
            messages = [{"role": "user", "content": synthesis_prompt}]
            async for chunk in self._client.complete("chat", messages):
                yield chunk

    async def _run_background(self, task: Task) -> None:
        """Run a background task submitted by a monitor daemon."""
        if task.goal == "__nightly_consolidation__" and self._memory_agent:
            try:
                await self._memory_agent.consolidate_today()
                self._store.update_status(task.id, TaskStatus.COMPLETED, result="ok")
            except Exception as e:
                logger.error("[ORCHESTRATOR] Nightly consolidation failed: %s", e)
                self._store.update_status(task.id, TaskStatus.FAILED, result=str(e))
            return

        self._store.update_status(task.id, TaskStatus.IN_PROGRESS)
        
        if task.created_by == "user":
            append_to_memory("user", task.goal)

        result_chunks: list[str] = []
        try:
            async for chunk in self._execute_task(task):
                result_chunks.append(chunk)
            
            final_text = "".join(result_chunks)
            self._store.update_status(task.id, TaskStatus.COMPLETED, result=final_text)

            if task.created_by in ("user", "proactive"):
                append_to_memory("assistant", strip_internal_tags(final_text))
        except Exception as e:
            logger.error("[ORCHESTRATOR] Background task %s failed: %s", task.id, e)
            self._store.update_status(task.id, TaskStatus.FAILED, result=str(e))

    async def _request_approval(self, task: Task) -> bool:
        """Notify all registered callbacks and return True if approved."""
        if not self._approval_callbacks:
            logger.warning(
                "[ORCHESTRATOR] Task %s is irreversible but no approval callbacks registered — cancelling.",
                task.id,
            )
            return False
        self._store.update_status(task.id, TaskStatus.AWAITING_APPROVAL)
        for cb in self._approval_callbacks:
            try:
                if await cb(task):
                    return True
            except Exception:
                pass
        return False

    def subscribe_to_bus(self, bus: "InternalEventBus") -> None:
        """Wire this orchestrator as a consumer of all monitor bus events."""
        from app.core.events import EventType
        bus.subscribe(EventType.TASK_REQUEST,   self._on_task_request)
        bus.subscribe(EventType.SYS_THRESHOLD,  self._on_sys_threshold)
        bus.subscribe(EventType.FS_CHANGE,      self._on_fs_change)
        bus.subscribe(EventType.MONITOR_STATUS, self._on_monitor_status)

    async def _on_task_request(self, event: "WadeEvent") -> None:
        from app.core.task_store import Task
        task = Task(goal=event.payload["goal"], created_by=event.source)
        await self.submit(task)

    async def _on_sys_threshold(self, event: "WadeEvent") -> None:
        from app.memory.episodes import get_episode_store, Episode
        episode = Episode(
            type="monitor_event",
            content=f"System threshold breach from {event.source}: {event.payload.get('alerts', [])}",
            tags=["system", "threshold"],
        )
        await asyncio.to_thread(get_episode_store().record, episode)

    async def _on_fs_change(self, event: "WadeEvent") -> None:
        from app.memory.episodes import get_episode_store, Episode
        name       = event.payload.get("name", "unknown")
        event_type = event.payload.get("event_type", "changed")
        episode = Episode(
            type="monitor_event",
            content=f"Filesystem {event_type}: {name}",
            tags=["filesystem", "change"],
        )
        await asyncio.to_thread(get_episode_store().record, episode)

    async def _on_monitor_status(self, event: "WadeEvent") -> None:
        if not event.payload.get("is_recovery"):
            return
        from app.memory.episodes import get_episode_store, Episode
        episode = Episode(
            type="monitor_event",
            content=(
                f"System recovery from {event.source}: "
                f"CPU {event.payload.get('cpu', '?')}%, RAM {event.payload.get('ram', '?')}%"
            ),
            tags=["system", "recovery"],
        )
        await asyncio.to_thread(get_episode_store().record, episode)

orchestrator = Orchestrator(task_store=TaskStore(TASKS_DB_PATH))