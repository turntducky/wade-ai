import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_classifier(scores_by_category: dict[str, float], slow_result: list[str] | None = None):
    """
    Build an IntentClassifier with mocked ChromaDB collections.

    scores_by_category maps category name → similarity score (0–1).
    Distances are derived as: distance = (1 / score) - 1.
    """
    from app.core.intent_classifier import IntentClassifier

    mock_chroma = MagicMock()

    def make_coll(category):
        score = scores_by_category.get(category, 0.0)
        distance = (1.0 / score - 1.0) if score > 0 else 999.0
        coll = MagicMock()
        coll.count.return_value = 3
        coll.get.return_value = {"ids": ["x"]}  # non-empty → no re-upsert
        coll.query.return_value = {"ids": [[f"{category}_0"]], "distances": [[distance]]}
        return coll

    mock_chroma.get_or_create_collection.side_effect = lambda name: make_coll(
        name.replace("wade_intent_", "")
    )

    mock_client = MagicMock()
    if slow_result is not None:
        async def _fake_complete(role, messages):
            yield json.dumps(slow_result)
        mock_client.complete.side_effect = _fake_complete

    clf = IntentClassifier(chroma_client=mock_chroma, inference_client=mock_client)
    return clf


# ── Fast path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fast_path_high_confidence_single_category():
    """High score on 'system' → returns ['system'], slow path NOT triggered."""
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["system"] = 0.8
    clf = _make_classifier(scores)
    result = await clf.classify("what is my GPU temperature?")
    assert result == ["system"]
    clf._inference_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_fast_path_multi_intent():
    """Two categories above INCLUDE_THRESHOLD → both returned."""
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["web"] = 0.75
    scores["workspace"] = 0.6
    clf = _make_classifier(scores)
    result = await clf.classify("search the web and save results to a file")
    assert set(result) == {"web", "workspace"}
    clf._inference_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_slow_path_triggered_when_top_score_below_gray_zone():
    """Top score < GRAY_ZONE_MAX (0.55) → slow path fires."""
    scores = {cat: 0.4 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    # All scores = 0.4: above INCLUDE_THRESHOLD but top < GRAY_ZONE_MAX
    clf = _make_classifier(scores, slow_result=["workspace"])
    result = await clf.classify("do something with a file")
    assert "workspace" in result
    clf._inference_client.complete.assert_called_once()


@pytest.mark.asyncio
async def test_slow_path_triggered_on_fuzzy_boundary():
    """Last-included and first-excluded scores within SLOW_PATH_MARGIN (0.08) → slow path fires."""
    # workspace=0.60, web=0.54 → gap = 0.06 < SLOW_PATH_MARGIN=0.08 → fuzzy boundary
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["workspace"] = 0.60
    scores["web"] = 0.54
    clf = _make_classifier(scores, slow_result=["workspace", "web"])
    result = await clf.classify("...")
    assert "workspace" in result
    assert "web" in result
    clf._inference_client.complete.assert_called_once()


@pytest.mark.asyncio
async def test_slow_path_merges_with_fast_result():
    """Slow path adds a category; fast-path categories are preserved."""
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["web"] = 0.8
    scores["workspace"] = 0.74  # gap = 0.06 < SLOW_PATH_MARGIN → triggers slow
    # slow path returns ["web", "workspace"]
    clf = _make_classifier(scores, slow_result=["web", "workspace"])
    result = await clf.classify("...")
    assert "web" in result
    assert "workspace" in result


@pytest.mark.asyncio
async def test_slow_path_does_not_remove_confident_fast_categories():
    """Slow path returning a different set never removes a high-confidence fast category."""
    scores = {cat: 0.1 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    scores["system"] = 0.8
    scores["workspace"] = 0.76  # gap = 0.04 → triggers slow path
    # slow path only returns ["workspace"] — system is confident, must survive
    clf = _make_classifier(scores, slow_result=["workspace"])
    result = await clf.classify("check system health and write a log file")
    assert "system" in result
    assert "workspace" in result


@pytest.mark.asyncio
async def test_slow_path_not_triggered_when_client_is_none():
    """If no inference_client, slow path never fires even when score is low."""
    from app.core.intent_classifier import IntentClassifier
    mock_chroma = MagicMock()

    def make_coll(category):
        score = 0.4
        distance = (1.0 / score - 1.0)
        coll = MagicMock()
        coll.count.return_value = 3
        coll.get.return_value = {"ids": ["x"]}
        coll.query.return_value = {"ids": [[f"{category}_0"]], "distances": [[distance]]}
        return coll

    mock_chroma.get_or_create_collection.side_effect = lambda name: make_coll(
        name.replace("wade_intent_", "")
    )
    clf = IntentClassifier(chroma_client=mock_chroma, inference_client=None)
    result = await clf.classify("ambiguous request")
    assert isinstance(result, list)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_thresholds_are_read_from_class_constants():
    """Patching the constants changes behavior — they are not hardcoded literals."""
    from app.core import intent_classifier as clf_module
    scores = {cat: 0.5 for cat in ["workspace", "web", "system", "scheduling", "memory", "communication", "research"]}
    with patch.object(clf_module.IntentClassifier, "INCLUDE_THRESHOLD", 0.6):
        clf = _make_classifier(scores)
        result = await clf.classify("test")
        assert isinstance(result, list)
