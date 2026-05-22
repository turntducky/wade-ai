import pytest

from datetime import datetime, timedelta

from app.memory.episodes import Episode, EpisodeStore

@pytest.fixture
def store(tmp_path) -> EpisodeStore:
    return EpisodeStore(tmp_path / "test_episodes.db")

def test_record_and_query_recent(store):
    ep = Episode(content="hello world", type="conversation", session_id="s1")
    store.record(ep)
    results = store.query_recent(limit=10)
    assert len(results) == 1
    assert results[0].content == "hello world"
    assert results[0].type == "conversation"
    assert results[0].session_id == "s1"

def test_query_recent_returns_newest_first(store):
    ep1 = Episode(content="first", type="conversation",
                  timestamp=datetime(2024, 1, 1, 0, 0, 0))
    ep2 = Episode(content="second", type="conversation",
                  timestamp=datetime(2024, 1, 1, 0, 0, 1))
    store.record(ep1)
    store.record(ep2)
    results = store.query_recent(limit=10)
    assert results[0].content == "second"
    assert results[1].content == "first"

def test_query_by_session(store):
    ep1 = Episode(content="session A", type="conversation", session_id="a")
    ep2 = Episode(content="session B", type="conversation", session_id="b")
    store.record(ep1)
    store.record(ep2)
    results = store.query_by_session("a")
    assert len(results) == 1
    assert results[0].session_id == "a"

def test_query_temporal(store):
    before = datetime.now() - timedelta(minutes=1)
    ep = Episode(content="recent event", type="task")
    store.record(ep)
    results = store.query_temporal(since=before)
    assert len(results) == 1
    assert results[0].content == "recent event"

def test_tags_roundtrip(store):
    ep = Episode(content="tagged", type="fact_extracted", tags=["python", "ai"])
    store.record(ep)
    retrieved = store.query_recent(limit=1)[0]
    assert retrieved.tags == ["python", "ai"]

def test_link_episodes(store):
    ep1 = Episode(content="ep1", type="conversation")
    ep2 = Episode(content="ep2", type="conversation")
    store.record(ep1)
    store.record(ep2)
    store.link(ep1.id, [ep2.id])
    all_eps = store.query_recent(limit=10)
    linked = next(e for e in all_eps if e.id == ep1.id)
    assert ep2.id in linked.linked_to

def test_get_by_type(store):
    store.record(Episode(content="fact", type="fact_extracted"))
    store.record(Episode(content="chat", type="conversation"))
    facts = store.get_by_type("fact_extracted")
    assert len(facts) == 1
    assert facts[0].type == "fact_extracted"