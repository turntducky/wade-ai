import pytest
import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

from app.core.orchestrator import Orchestrator
from app.core.task_store import Task, TaskStore
from app.services.model_router import ModelRouter
from app.services.inference_client import InferenceClient

@pytest.fixture
def store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")

@pytest.fixture
def client() -> InferenceClient:
    return InferenceClient(router=ModelRouter({"fast": "qwen2.5:3b", "tools": "qwen2.5:7b", "planner": "qwen2.5:14b"}))

@pytest.fixture
def orchestrator(store, client) -> Orchestrator:
    return Orchestrator(task_store=store, inference_client=client)

@pytest.fixture
def orchestrator_instance(store, client) -> Orchestrator:
    return Orchestrator(task_store=store, inference_client=client)

@pytest.mark.asyncio
async def test_process_creates_task_and_streams_response(orchestrator, store):
    async def fake_execute(task, session_id=None, **kwargs):
        yield "hello "
        yield "world"

    with patch.object(orchestrator, "_execute_task", side_effect=fake_execute):
        chunks = []
        async for chunk in orchestrator.process("say hello"):
            chunks.append(chunk)

    assert "".join(chunks) == "hello world"

@pytest.mark.asyncio
async def test_process_marks_task_completed_on_success(orchestrator, store):
    async def fake_execute(task, session_id=None, **kwargs):
        yield "done"

    with patch.object(orchestrator, "_execute_task", side_effect=fake_execute):
        tasks_before = store.list_active()
        async for _ in orchestrator.process("do something"):
            pass

    active = store.list_active()
    assert len(active) == 0

@pytest.mark.asyncio
async def test_process_marks_task_failed_on_exception(orchestrator, store):
    async def failing_execute(task, session_id=None, **kwargs):
        raise RuntimeError("something broke")
        yield

    with patch.object(orchestrator, "_execute_task", side_effect=failing_execute):
        chunks = []
        async for chunk in orchestrator.process("fail me"):
            chunks.append(chunk)

    assert any("failed" in c.lower() or "Task failed" in c for c in chunks)
    assert len(store.list_active()) == 0

@pytest.mark.asyncio
async def test_submit_creates_background_task(orchestrator, store):
    task = Task(goal="background work")
    executed = []

    async def fake_run_background(t):
        executed.append(t.id)

    with patch.object(orchestrator, "_run_background", side_effect=fake_run_background):
        import asyncio
        with patch("asyncio.create_task") as mock_create:
            await orchestrator.submit(task)
            mock_create.assert_called_once()

    saved = store.get(task.id)
    assert saved is not None
    assert saved.goal == "background work"

@pytest.mark.asyncio
async def test_process_calls_memory_agent_after_success(orchestrator, store):
    """Memory agent extract() should be scheduled as a fire-and-forget task after success."""
    memory_agent = MagicMock()
    memory_agent.extract = AsyncMock(return_value=None)
    orchestrator.set_memory_agent(memory_agent)

    async def fake_execute(task, session_id=None, **kwargs):
        yield "result text"

    created_tasks = []

    original_create_task = asyncio.create_task

    def capture_create_task(coro, *args, **kwargs):
        t = original_create_task(coro, *args, **kwargs)
        created_tasks.append(t)
        return t

    with patch.object(orchestrator, "_execute_task", side_effect=fake_execute):
        with patch("asyncio.create_task", side_effect=capture_create_task):
            async for _ in orchestrator.process("remember this", session_id="sess-1"):
                pass

    await asyncio.gather(*created_tasks, return_exceptions=True)

    memory_agent.extract.assert_called_once_with("result text", session_id="sess-1", user_text="remember this")

@pytest.mark.asyncio
async def test_process_skips_memory_agent_for_system_calls(orchestrator, store):
    """Memory extraction should be skipped when is_system=True."""
    memory_agent = MagicMock()
    memory_agent.extract = AsyncMock(return_value=None)
    orchestrator.set_memory_agent(memory_agent)

    async def fake_execute(task, session_id=None, **kwargs):
        yield "system output"

    with patch.object(orchestrator, "_execute_task", side_effect=fake_execute):
        async for _ in orchestrator.process("system task", is_system=True):
            pass

    memory_agent.extract.assert_not_called()

@pytest.mark.asyncio
async def test_list_recent_tasks_returns_tasks(orchestrator, store):
    """list_recent_tasks() should delegate to the store and return saved tasks."""
    async def fake_execute(task, session_id=None, **kwargs):
        yield "ok"

    with patch.object(orchestrator, "_execute_task", side_effect=fake_execute):
        async for _ in orchestrator.process("task one"):
            pass

    async def fake_execute2(task, session_id=None, **kwargs):
        yield "ok"

    with patch.object(orchestrator, "_execute_task", side_effect=fake_execute2):
        async for _ in orchestrator.process("task two"):
            pass

    recent = orchestrator.list_recent_tasks(limit=10)
    goals = [t.goal for t in recent]
    assert "task one" in goals
    assert "task two" in goals

@pytest.mark.asyncio
async def test_process_retries_on_connection_error(orchestrator_instance):
    """process() must retry up to 3 times when InferenceClient raises ConnectError."""
    import httpx
    call_count = 0

    mock_executor_cls = MagicMock()

    async def flaky_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ConnectError("connection refused")
        yield "recovered"

    mock_executor = MagicMock()
    mock_executor.execute = flaky_execute
    mock_executor_cls.return_value = mock_executor

    mock_planner = MagicMock()
    mock_planner.needs_planning = MagicMock(return_value=False)

    orchestrator_instance.set_planner(mock_planner)
    orchestrator_instance.set_executor_cls(mock_executor_cls)

    chunks = []
    with patch("asyncio.sleep", new_callable=AsyncMock):
        async for chunk in orchestrator_instance.process("test", session_id="s1"):
            chunks.append(chunk)

    assert "recovered" in chunks
    assert call_count == 3

@pytest.mark.asyncio
async def test_orchestrator_has_critic_attribute(tmp_path):
    from app.agents.critic import CriticAgent
    store = TaskStore(tmp_path / "t.db")
    orch = Orchestrator(task_store=store)
    assert hasattr(orch, "_critic")
    assert isinstance(orch._critic, CriticAgent)

@pytest.mark.asyncio
async def test_wave_levels_with_string_depends_on(tmp_path):
    """_wave_levels must work when depends_on contains task ID strings."""
    store = TaskStore(tmp_path / "t.db")
    orch = Orchestrator(task_store=store)

    t0 = Task(goal="step 0")
    t1 = Task(goal="step 1")
    t2 = Task(goal="step 2", depends_on=[t0.id, t1.id])
    waves = orch._wave_levels([t0, t1, t2])
    assert sorted(waves[0]) == [0, 1]
    assert waves[1] == [2]

class TestOrchestratorBusHandlers:
    """Tests for the subscribe_to_bus wiring added in the event-bus refactor."""

    @pytest.fixture
    def orch(self):
        from app.core.orchestrator import Orchestrator
        o = Orchestrator()
        o.submit = AsyncMock()
        return o

    @pytest.fixture
    def mock_bus(self):
        from app.core.events import InternalEventBus
        bus = MagicMock(spec=InternalEventBus)
        bus.subscribe = MagicMock()
        return bus

    def test_subscribe_to_bus_registers_four_handlers(self, orch, mock_bus):
        orch.subscribe_to_bus(mock_bus)
        assert mock_bus.subscribe.call_count == 4

    @pytest.mark.asyncio
    async def test_on_task_request_calls_submit(self, orch):
        from app.core.events import EventType, WadeEvent
        event = WadeEvent(
            event_type=EventType.TASK_REQUEST,
            payload={"goal": "run diagnostics"},
            source="monitor:system",
        )
        await orch._on_task_request(event)
        orch.submit.assert_called_once()
        task = orch.submit.call_args[0][0]
        assert task.goal       == "run diagnostics"
        assert task.created_by == "monitor:system"

    @pytest.mark.asyncio
    async def test_on_sys_threshold_records_episode(self, orch):
        from app.core.events import EventType, WadeEvent
        event = WadeEvent(
            event_type=EventType.SYS_THRESHOLD,
            payload={"alerts": ["CPU at 95.0%"], "cpu": 95.0, "ram": 50.0, "disk": 40.0},
            source="monitor:system",
        )
        with patch("app.core.orchestrator.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            await orch._on_sys_threshold(event)
        mock_thread.assert_called_once()
        episode = mock_thread.call_args[0][1]
        assert episode.type == "monitor_event"
        assert "threshold" in episode.tags

    @pytest.mark.asyncio
    async def test_on_fs_change_records_episode(self, orch):
        from app.core.events import EventType, WadeEvent
        event = WadeEvent(
            event_type=EventType.FS_CHANGE,
            payload={"name": "PROJECTS.md", "event_type": "modified"},
            source="monitor:filesystem",
        )
        with patch("app.core.orchestrator.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            await orch._on_fs_change(event)
        mock_thread.assert_called_once()
        episode = mock_thread.call_args[0][1]
        assert episode.type == "monitor_event"
        assert "PROJECTS.md" in episode.content
        assert "filesystem"  in episode.tags

    @pytest.mark.asyncio
    async def test_on_monitor_status_records_episode_on_recovery(self, orch):
        from app.core.events import EventType, WadeEvent
        event = WadeEvent(
            event_type=EventType.MONITOR_STATUS,
            payload={"cpu": 20.0, "ram": 30.0, "is_recovery": True},
            source="monitor:system",
        )
        with patch("app.core.orchestrator.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            await orch._on_monitor_status(event)
        mock_thread.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_monitor_status_ignores_non_recovery(self, orch):
        from app.core.events import EventType, WadeEvent
        event = WadeEvent(
            event_type=EventType.MONITOR_STATUS,
            payload={"cpu": 20.0, "ram": 30.0, "is_recovery": False},
            source="monitor:system",
        )
        with patch("app.core.orchestrator.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            await orch._on_monitor_status(event)
        mock_thread.assert_not_called()