"""Tests for the unified search_entities tool.

Regression coverage for a defect where search_entities passed a `search_mode`
keyword to semantic_search, which accepts `hybrid_search`. The resulting
TypeError was caught by the surrounding fallback handler, so "semantic" and
"hybrid" modes silently degraded to keyword search with no visible error.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.tools.unified import search_entities

SEMANTIC_RESULTS = [
    {
        "entity_id": "light.living_room",
        "entity": {"state": "on", "attributes": {"friendly_name": "Living Room"}},
        "similarity_score": 0.9123,
        "explanation": "Semantic match on 'living room'",
    }
]


class TestSemanticModes:
    """search_mode must reach the vector backend as hybrid_search."""

    @pytest.mark.parametrize(
        ("search_mode", "expected_hybrid"),
        [("semantic", False), ("hybrid", True)],
    )
    async def test_search_mode_maps_to_hybrid_search_flag(self, search_mode, expected_hybrid):
        mock_search = AsyncMock(return_value=SEMANTIC_RESULTS)
        with patch("app.tools.entities.semantic_search", new=mock_search):
            result = await search_entities(query="living room lights", search_mode=search_mode)

        assert mock_search.await_count == 1
        assert mock_search.await_args.kwargs["hybrid_search"] is expected_hybrid
        assert "search_mode" not in mock_search.await_args.kwargs
        assert result["search_mode"] == search_mode

    async def test_semantic_results_are_returned_with_scores(self):
        """A successful semantic search must surface similarity data, not fall back."""
        with patch(
            "app.tools.entities.semantic_search", new=AsyncMock(return_value=SEMANTIC_RESULTS)
        ):
            result = await search_entities(query="living room lights", search_mode="hybrid")

        assert result["count"] == 1
        entry = result["results"][0]
        assert entry["entity_id"] == "light.living_room"
        assert entry["similarity"] == 0.912
        assert entry["match_reason"] == "Semantic match on 'living room'"
        assert result["domains"] == {"light": 1}

    async def test_search_parameters_are_forwarded(self):
        mock_search = AsyncMock(return_value=[])
        with patch("app.tools.entities.semantic_search", new=mock_search):
            await search_entities(
                query="lights",
                domain="light",
                area_id="kitchen",
                limit=7,
                similarity_threshold=0.85,
                search_mode="semantic",
            )

        kwargs = mock_search.await_args.kwargs
        assert kwargs["domain"] == "light"
        assert kwargs["area_id"] == "kitchen"
        assert kwargs["limit"] == 7
        assert kwargs["similarity_threshold"] == 0.85

    async def test_backend_failure_falls_back_to_keyword(self):
        """A genuine backend error should still degrade gracefully."""
        entities = [{"entity_id": "light.kitchen", "state": "on", "attributes": {}}]
        with patch(
            "app.tools.entities.semantic_search",
            new=AsyncMock(side_effect=RuntimeError("vector backend down")),
        ):
            with patch("app.tools.entities.get_entities", new=AsyncMock(return_value=entities)):
                result = await search_entities(query="kitchen", search_mode="semantic")

        assert result["search_mode"] == "keyword"
        assert result["count"] == 1
        assert "vector backend down" in result["results"][0]["match_reason"]


class TestSearchModeValidation:
    async def test_unknown_search_mode_is_rejected(self):
        result = await search_entities(query="x", search_mode="bogus")

        assert "Invalid search_mode" in result["error"]
        assert result["count"] == 0
        assert result["results"] == []

    async def test_keyword_mode_does_not_touch_vector_backend(self):
        entities = [{"entity_id": "light.kitchen", "state": "on", "attributes": {}}]
        mock_search = AsyncMock(return_value=[])
        with patch("app.tools.entities.semantic_search", new=mock_search):
            with patch("app.tools.unified.get_entities", new=AsyncMock(return_value=entities)):
                result = await search_entities(query="kitchen", search_mode="keyword")

        mock_search.assert_not_awaited()
        assert result["search_mode"] == "keyword"
