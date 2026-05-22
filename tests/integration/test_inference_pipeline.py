import os
import pytest

SKIP_REASON = "Set WADE_INTEGRATION_TESTS=1 to run integration tests (requires live Ollama)"
requires_ollama = pytest.mark.skipif(
    os.getenv("WADE_INTEGRATION_TESTS") != "1",
    reason=SKIP_REASON,
)

@requires_ollama
@pytest.mark.asyncio
async def test_chat_returns_response():
    """InferenceClient.chat() returns a non-empty string against live Ollama."""
    from app.services.inference_client import InferenceClient
    client = InferenceClient()
    
    result = await client.chat(
        messages=[{"role": "user", "content": "Reply with exactly: INTEGRATION_OK"}],
        model_role="default"
    )
    
    assert isinstance(result, str)
    assert len(result) > 0
    assert "INTEGRATION_OK" in result

@requires_ollama
@pytest.mark.asyncio
async def test_orchestrator_processes_simple_prompt():
    """Orchestrator.process() completes a simple prompt against live Ollama."""
    from app.core.task_store import TaskStore, TaskStatus
    from app.core.orchestrator import Orchestrator
    from app.services.inference_client import InferenceClient
    from app.agents.planner import PlannerAgent
    from app.agents.executor import ExecutorAgent
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    client = InferenceClient()
    store = TaskStore(db_path=db_path)
    orch = Orchestrator(task_store=store, inference_client=client)
    orch.set_planner(PlannerAgent(client))
    orch.set_executor_cls(ExecutorAgent)

    chunks = []
    async for chunk in orch.process("Say hello in one word", session_id="integration-test"):
        chunks.append(chunk)

    response = "".join(chunks)
    assert len(response) > 0

    tasks = orch.list_recent_tasks(limit=1)
    assert tasks[0].status == TaskStatus.COMPLETED