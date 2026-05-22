import pytest

from unittest.mock import AsyncMock, MagicMock, patch

from app.core.task_store import Task
from app.agents.executor import ExecutorAgent
from app.services.model_router import ModelRouter
from app.services.inference_client import InferenceClient

def make_executor() -> ExecutorAgent:
    router = ModelRouter({"tools": "qwen2.5:7b", "fast": "qwen2.5:3b"})
    client = InferenceClient(router=router)
    return ExecutorAgent(client)

@pytest.mark.asyncio
async def test_execute_simple_task_no_tool_calls():
    executor = make_executor()

    async def fake_complete(role, messages):
        yield "The answer is 42."

    with patch("app.agents.executor._personality") as mock_personality, \
         patch("app.agents.executor.get_system_location", return_value=("Local", "UTC")), \
         patch("app.agents.executor.load_recent_memory", return_value=""), \
         patch("app.agents.executor.read_core_memory", return_value=""), \
         patch("app.agents.executor._get_tools_for_task", return_value=([], "")):

        mock_personality.chroma_client = None
        mock_personality.get_core_identity_context.return_value = ""
        mock_personality.get_relevant_workspace_context.return_value = ""
        mock_personality.get_tools_instructions.return_value = ""

        with patch.object(executor._client, "complete", new=fake_complete):
            chunks = []
            async for chunk in executor.execute(Task(goal="what is 6*7?")):
                chunks.append(chunk)

    assert "".join(chunks) == "The answer is 42."

@pytest.mark.asyncio
async def test_execute_calls_tool_then_returns_final_response():
    executor = make_executor()

    tool_call = [{"function": {"name": "web_search", "arguments": {"query": "test"}}}]
    call_count = 0

    async def fake_chat(role, messages, tools=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ("", tool_call)
        return ("Search results found.", [])

    with patch("app.agents.executor._personality") as mock_personality, \
         patch("app.agents.executor.get_system_location", return_value=("Local", "UTC")), \
         patch("app.agents.executor.load_recent_memory", return_value=""), \
         patch("app.agents.executor.read_core_memory", return_value=""), \
         patch("app.agents.executor._get_tools_for_task", return_value=([{"function": {"name": "web_search"}}], "")), \
         patch("app.agents.executor.execute_tool", new_callable=AsyncMock, return_value="result text"):

        mock_personality.chroma_client = None
        mock_personality.get_core_identity_context.return_value = ""
        mock_personality.get_relevant_workspace_context.return_value = ""
        mock_personality.get_tools_instructions.return_value = ""

        with patch.object(executor._client, "chat", side_effect=fake_chat):
            chunks = []
            async for chunk in executor.execute(Task(goal="search the web")):
                chunks.append(chunk)

    result = "".join(chunks)
    assert "<tool_exec name='web_search' />" in result
    assert "Search results found." in result
    assert call_count == 2

@pytest.mark.asyncio
async def test_execute_stops_at_max_tool_calls():
    from app.agents.executor import MAX_TOOL_CALLS
    executor = make_executor()

    chat_call_count = 0

    async def always_different_tool_call(role, messages, tools=None):
        nonlocal chat_call_count
        chat_call_count += 1
        tool_call = [{"function": {"name": "web_search", "arguments": {"query": f"loop_{chat_call_count}"}}}]
        return ("", tool_call)

    async def fake_complete(role, messages):
        yield "Maximum reasoning depth reached."

    with patch("app.agents.executor._personality") as mock_personality, \
         patch("app.agents.executor.get_system_location", return_value=("Local", "UTC")), \
         patch("app.agents.executor.load_recent_memory", return_value=""), \
         patch("app.agents.executor.read_core_memory", return_value=""), \
         patch("app.agents.executor._get_tools_for_task", return_value=([{"function": {"name": "web_search"}}], "")), \
         patch("app.agents.executor.execute_tool", new_callable=AsyncMock, return_value="result"):

        mock_personality.chroma_client = None
        mock_personality.get_core_identity_context.return_value = ""
        mock_personality.get_relevant_workspace_context.return_value = ""
        mock_personality.get_tools_instructions.return_value = ""

        with patch.object(executor._client, "chat", side_effect=always_different_tool_call), \
             patch.object(executor._client, "complete", new=fake_complete):
            chunks = []
            async for chunk in executor.execute(Task(goal="loop forever")):
                chunks.append(chunk)

    assert chat_call_count == MAX_TOOL_CALLS + 1
    result = "".join(chunks)
    assert "maximum reasoning depth" in result.lower()

@pytest.mark.asyncio
async def test_executor_collects_traces_after_tool_call():
    """executor.traces is populated with one ToolTrace per tool call."""
    from app.agents.critic import ToolTrace

    client = MagicMock()
    client.chat = AsyncMock(side_effect=[
        ("", [{"function": {"name": "calculator", "arguments": {"expression": "2+2"}}}]),
        ("The answer is 4.", []),
    ])

    with patch("app.agents.executor.execute_tool", new=AsyncMock(return_value="4")), \
         patch("app.agents.executor.load_all_skills"), \
         patch("app.agents.executor.get_dynamic_tools", return_value=([], {})), \
         patch("app.agents.executor._get_tools_for_task", return_value=([{"function": {"name": "calculator"}}], "tool ctx")), \
         patch("app.agents.executor.load_recent_memory", return_value=""), \
         patch("app.agents.executor.read_core_memory", return_value=""), \
         patch("app.agents.executor.get_tool_risk", return_value="low"), \
         patch("app.agents.executor._personality") as mock_personality, \
         patch("app.agents.executor.get_system_location", return_value=("Local", "UTC")):

        mock_personality.chroma_client = None
        mock_personality.get_core_identity_context.return_value = ""
        mock_personality.get_relevant_workspace_context.return_value = ""
        mock_personality.get_tools_instructions.return_value = ""

        executor = ExecutorAgent(client)
        task = MagicMock()
        task.goal = "what is 2+2"
        task.expected_outcome = None

        chunks = []
        async for chunk in executor.execute(task):
            chunks.append(chunk)

    assert len(executor.traces) == 1
    trace = executor.traces[0]
    assert isinstance(trace, ToolTrace)
    assert trace.tool_name == "calculator"
    assert trace.exit_status == "success"
    assert trace.duration_ms >= 0
    assert trace.risk in ("low", "medium", "high")