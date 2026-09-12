"""Tests that unified tool results match the schemas published to MCP clients.

FastMCP derives each tool's outputSchema from its return annotation, so a tool
declared to return a list must not return an error dict (and vice versa) — a
client validating against the schema would reject the response.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.tools.unified import get_item_entities, get_system_data, list_items


class TestPublishedSchemas:
    """The declared annotations are what clients receive as outputSchema."""

    async def test_list_returning_tools_declare_arrays(self):
        from app import server

        tools = {t.name: t for t in await server.mcp.list_tools()}
        for name in ("list_items", "get_item_entities"):
            schema = tools[name].outputSchema
            assert schema["properties"]["result"]["type"] == "array", name


class TestListItemsErrorShape:
    async def test_invalid_item_type_returns_a_list(self):
        result = await list_items(item_type="not_a_type")

        assert isinstance(result, list)
        assert "Invalid item_type" in result[0]["error"]

    async def test_exception_returns_a_list(self):
        with patch.dict(
            "app.tools.unified.LIST_FUNCTIONS",
            {"automation": AsyncMock(side_effect=RuntimeError("boom"))},
        ):
            result = await list_items(item_type="automation")

        assert isinstance(result, list)
        assert "boom" in result[0]["error"]

    async def test_success_returns_a_list(self):
        items = [{"id": "a", "alias": "Morning"}]
        with patch.dict(
            "app.tools.unified.LIST_FUNCTIONS", {"automation": AsyncMock(return_value=items)}
        ):
            result = await list_items(item_type="automation")

        assert result == items


class TestGetItemEntitiesErrorShape:
    async def test_invalid_item_type_returns_a_list(self):
        result = await get_item_entities(item_type="not_a_type", item_id="x")

        assert isinstance(result, list)
        assert "Invalid item_type" in result[0]["error"]

    async def test_exception_returns_a_list(self):
        with patch(
            "app.tools.unified.devices.get_device_entities",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await get_item_entities(item_type="device", item_id="d1")

        assert isinstance(result, list)
        assert "boom" in result[0]["error"]


class TestGetSystemDataHistoryShape:
    """History must be returned as the declared dict, not the raw API list."""

    async def test_history_is_wrapped_in_a_dict(self):
        raw = [[{"entity_id": "light.k", "state": "on", "last_changed": "2026-01-01T00:00:00Z"}]]
        with patch("app.tools.system.get_entity_history", new=AsyncMock(return_value=raw)):
            result = await get_system_data(data_type="history", entity_id="light.k")

        assert isinstance(result, dict)
        assert result["entity_id"] == "light.k"
        assert result["count"] == 1
        assert result["states"][0]["state"] == "on"

    async def test_history_requires_entity_id(self):
        result = await get_system_data(data_type="history")

        assert "entity_id is required" in result["error"]


class TestManageItemZoneValidation:
    """create_zone needs numeric coordinates; missing ones must be reported."""

    async def test_missing_coordinates_are_reported(self):
        from app.tools.unified import manage_item

        result = await manage_item(action="create", item_type="zone", config={"name": "Home"})

        assert "Missing required zone field" in result["error"]
        for field in ("latitude", "longitude", "radius"):
            assert field in result["error"]

    async def test_coordinates_are_coerced_to_float(self):
        from app.tools.unified import manage_item

        create = AsyncMock(return_value={"zone_id": "zone.home"})
        with patch("app.tools.unified.zones.create_zone", new=create):
            await manage_item(
                action="create",
                item_type="zone",
                config={"name": "Home", "latitude": "52.1", "longitude": "21.0", "radius": "100"},
            )

        args = create.await_args.args
        assert args[1] == pytest.approx(52.1)
        assert args[2] == pytest.approx(21.0)
        assert args[3] == pytest.approx(100.0)


class TestLongTermStatisticsTool:
    """The ported long-term statistics must be reachable through get_statistics."""

    async def test_long_term_type_uses_the_recorder(self):
        from app.tools.unified import get_statistics

        call = AsyncMock(return_value={"entity_id": "sensor.power", "statistics": [1]})
        with patch("app.tools.unified.statistics.get_long_term_statistics", call):
            result = await get_statistics(type="long_term", entity_id="sensor.power", period="day")

        assert call.await_args.kwargs["period"] == "day"
        assert result["statistics"] == [1]

    async def test_long_term_with_explicit_range(self):
        from app.tools.unified import get_statistics

        call = AsyncMock(return_value={"entity_id": "sensor.power", "statistics": []})
        with patch("app.tools.unified.statistics.get_entity_statistics_range", call):
            await get_statistics(
                type="long_term",
                entity_id="sensor.power",
                start_time="2025-01-01",
                end_time="2026-01-01",
                period="month",
            )

        assert call.await_args.args[1:] == ("2025-01-01", "2026-01-01", "month")

    async def test_long_term_requires_entity_id(self):
        from app.tools.unified import get_statistics

        result = await get_statistics(type="long_term")
        assert "entity_id is required" in result["error"]

    async def test_invalid_type_lists_long_term(self):
        from app.tools.unified import get_statistics

        result = await get_statistics(type="bogus")
        assert "long_term" in result["error"]


class TestHistoryRangeTool:
    """Date-range history must be reachable through get_system_data."""

    async def test_explicit_window_uses_range_api(self):
        from app.tools.unified import get_system_data

        call = AsyncMock(return_value=[[{"state": "on"}, {"state": "off"}]])
        with patch("app.tools.unified.get_entity_history_range", call):
            result = await get_system_data(
                data_type="history",
                entity_id="light.kitchen",
                start_time="2026-01-15",
                end_time="2026-01-16",
            )

        assert call.await_args.args == ("light.kitchen", "2026-01-15", "2026-01-16")
        assert result["count"] == 2
        assert result["start_time"] == "2026-01-15"

    async def test_hours_is_honoured_without_a_window(self):
        from app.tools.unified import get_system_data

        call = AsyncMock(return_value={"entity_id": "light.kitchen", "states": [], "count": 0})
        with patch("app.tools.unified.system_tools.get_history", call):
            await get_system_data(data_type="history", entity_id="light.kitchen", hours=72)

        assert call.await_args.args == ("light.kitchen", 72)

    async def test_range_errors_propagate(self):
        from app.tools.unified import get_system_data

        call = AsyncMock(return_value={"error": "start_time must be before end_time"})
        with patch("app.tools.unified.get_entity_history_range", call):
            result = await get_system_data(
                data_type="history",
                entity_id="light.kitchen",
                start_time="2026-01-16",
                end_time="2026-01-15",
            )
        assert "start_time must be before" in result["error"]
