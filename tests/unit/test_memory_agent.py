import pytest

from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture
def agent(tmp_path):
    from app.agents.memory_agent import MemoryAgent
    from app.memory.episodes import EpisodeStore
    mock_client = MagicMock()
    mock_client.chat = AsyncMock()
    store = EpisodeStore(tmp_path / "test_episodes.db")
    return MemoryAgent(client=mock_client, episode_store=store)

@pytest.mark.asyncio
async def test_extract_records_facts(agent):
    agent._client.chat.return_value = ('["User prefers dark mode", "User works in Python"]', [])
    await agent.extract("User said they prefer dark mode and work in Python", session_id="s1")
    facts = agent._episode_store.get_by_type("fact_extracted")
    assert len(facts) == 2
    assert any("dark mode" in f.content for f in facts)

@pytest.mark.asyncio
async def test_extract_empty_list_records_nothing(agent):
    agent._client.chat.return_value = ('[]', [])
    await agent.extract("sounds good", session_id="s1")
    facts = agent._episode_store.get_by_type("fact_extracted")
    assert len(facts) == 0

@pytest.mark.asyncio
async def test_extract_bad_json_does_not_raise(agent):
    agent._client.chat.return_value = ('this is not json', [])
    await agent.extract("some conversation", session_id="s1")
    facts = agent._episode_store.get_by_type("fact_extracted")
    assert len(facts) == 0

@pytest.mark.asyncio
async def test_extract_strips_markdown_fences(agent):
    agent._client.chat.return_value = ('```json\n["user likes coffee"]\n```', [])
    await agent.extract("User mentioned they like coffee", session_id="s1")
    facts = agent._episode_store.get_by_type("fact_extracted")
    assert len(facts) == 1

@pytest.mark.asyncio
async def test_extract_records_conversation_episode(agent):
    agent._client.chat.return_value = ('[]', [])
    await agent.extract("hello there", session_id="sess-99")
    convs = agent._episode_store.get_by_type("conversation")
    assert len(convs) == 1
    assert convs[0].session_id == "sess-99"