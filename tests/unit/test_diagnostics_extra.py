"""Tests for the read-only diagnostic tools ported from nnion/hass-mcp.

All three are off by default; these tests cover the behaviour and the fact that
they stay off unless configured.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.api.traces import get_automation_traces_data
from app.api.updates import (
    HACS_CATEGORY_DISPLAY,
    HACS_CATEGORY_MAP,
    get_available_updates,
    get_hacs_info,
)
from app.core.ws import HassWebSocketError
from app.tools.registry import DEFAULT_ENABLED, TOOLS_BY_NAME, resolve_enabled

PORTED_TOOLS = (
    "get_automation_traces_tool",
    "get_available_updates_tool",
    "get_hacs_info_tool",
)


@pytest.fixture(autouse=True)
def _token():
    """handle_api_errors short-circuits without a token, before the body runs."""
    with patch("app.core.decorators.HA_TOKEN", "test-token"):
        yield


class TestDisabledByDefault:
    @pytest.mark.parametrize("name", PORTED_TOOLS)
    def test_off_by_default(self, name):
        assert TOOLS_BY_NAME[name].default is False
        assert name not in DEFAULT_ENABLED

    @pytest.mark.parametrize("name", PORTED_TOOLS)
    def test_can_be_enabled(self, name):
        specs, unknown = resolve_enabled([name])
        assert [s.name for s in specs] == [name]
        assert unknown == []

    async def test_not_in_the_live_surface(self):
        from app import server

        live = {tool.name for tool in await server.mcp.list_tools()}
        for name in PORTED_TOOLS:
            assert name not in live


class TestGetAvailableUpdates:
    async def test_only_pending_updates_are_returned(self):
        entities = [
            {
                "entity_id": "update.home_assistant_core",
                "state": "on",
                "attributes": {
                    "title": "Home Assistant Core",
                    "installed_version": "2026.1.0",
                    "latest_version": "2026.2.0",
                    "release_url": "https://example.invalid/release",
                },
            },
            {
                "entity_id": "update.some_addon",
                "state": "off",
                "attributes": {"title": "Already current"},
            },
        ]
        with patch("app.api.updates.get_entities", new=AsyncMock(return_value=entities)):
            result = await get_available_updates()

        assert result["count"] == 1
        update = result["updates"][0]
        assert update["entity_id"] == "update.home_assistant_core"
        assert update["installed_version"] == "2026.1.0"
        assert update["latest_version"] == "2026.2.0"
        assert update["in_progress"] is False

    async def test_falls_back_to_friendly_name(self):
        entities = [
            {
                "entity_id": "update.thing",
                "state": "on",
                "attributes": {"friendly_name": "Thing"},
            }
        ]
        with patch("app.api.updates.get_entities", new=AsyncMock(return_value=entities)):
            result = await get_available_updates()

        assert result["updates"][0]["title"] == "Thing"

    async def test_everything_current(self):
        with patch("app.api.updates.get_entities", new=AsyncMock(return_value=[])):
            result = await get_available_updates()

        assert result == {"count": 0, "updates": []}

    async def test_entity_error_is_propagated(self):
        with patch(
            "app.api.updates.get_entities",
            new=AsyncMock(return_value={"error": "Connection error"}),
        ):
            result = await get_available_updates()

        assert result["error"] == "Connection error"
        assert result["updates"] == []


class TestGetHacsInfo:
    REPOS = [
        {
            "name": "Mushroom",
            "full_name": "piitaya/lovelace-mushroom",
            "description": "Mushroom cards",
            "category": "plugin",
            "installed": True,
            "installed_version": "3.0.0",
            "available_version": "3.1.0",
            "pending_upgrade": True,
        },
        {
            "name": "Alarmo",
            "full_name": "nielsfaber/alarmo",
            "description": "Alarm system",
            "category": "integration",
            "installed": False,
            "available_version": "1.9.5",
        },
    ]

    async def test_installed_only_by_default(self):
        with patch("app.api.updates.call_ws", new=AsyncMock(side_effect=[{}, self.REPOS])):
            result = await get_hacs_info()

        assert result["count"] == 1
        assert result["repositories"][0]["name"] == "Mushroom"
        assert result["repositories"][0]["pending_update"] is True

    async def test_lovelace_category_is_displayed_not_plugin(self):
        """HACS calls it "plugin" internally; users know it as "lovelace"."""
        with patch("app.api.updates.call_ws", new=AsyncMock(side_effect=[{}, self.REPOS])):
            result = await get_hacs_info()

        assert result["repositories"][0]["category"] == "lovelace"

    async def test_searching_the_whole_store(self):
        with patch("app.api.updates.call_ws", new=AsyncMock(side_effect=[{}, self.REPOS])):
            result = await get_hacs_info(installed_only=False)

        assert result["total_matches"] == 2
        # Sorted by name: Alarmo before Mushroom
        assert [r["name"] for r in result["repositories"]] == ["Alarmo", "Mushroom"]

    async def test_query_filters_across_fields(self):
        with patch("app.api.updates.call_ws", new=AsyncMock(side_effect=[{}, self.REPOS])):
            result = await get_hacs_info(query="nielsfaber", installed_only=False)

        assert [r["name"] for r in result["repositories"]] == ["Alarmo"]

    async def test_category_is_translated_for_the_request(self):
        call_ws = AsyncMock(side_effect=[{}, []])
        with patch("app.api.updates.call_ws", call_ws):
            await get_hacs_info(category="lovelace")

        assert call_ws.await_args.kwargs["categories"] == ["plugin"]

    async def test_limit_is_applied_but_total_reported(self):
        with patch("app.api.updates.call_ws", new=AsyncMock(side_effect=[{}, self.REPOS])):
            result = await get_hacs_info(installed_only=False, limit=1)

        assert result["count"] == 1
        assert result["total_matches"] == 2

    async def test_missing_hacs_reports_clearly(self):
        with patch(
            "app.api.updates.call_ws",
            new=AsyncMock(side_effect=HassWebSocketError("unknown command")),
        ):
            result = await get_hacs_info()

        assert "HACS is not installed" in result["error"]
        assert result["repositories"] == []

    def test_category_maps_round_trip(self):
        for display, internal in HACS_CATEGORY_MAP.items():
            assert HACS_CATEGORY_DISPLAY[internal] == display


class TestGetAutomationTraces:
    async def test_rejects_non_automation_entity(self):
        result = await get_automation_traces_data("light.kitchen")

        assert "must start with" in str(result)

    async def test_rejects_bad_order(self):
        result = await get_automation_traces_data("automation.x", order="sideways")

        assert "order must be" in str(result)

    async def test_listing_uses_trace_list(self):
        traces = [
            {"run_id": "1", "timestamp": {"start": "2026-01-01T00:00:00Z"}, "state": "stopped"}
        ]

        async def fake_call_ws(message_type, **kwargs):
            if message_type == "config/entity_registry/get":
                return {"unique_id": "uid-1"}
            if message_type == "trace/list":
                return traces
            return {}

        with patch("app.api.traces.call_ws", new=AsyncMock(side_effect=fake_call_ws)):
            result = await get_automation_traces_data("automation.motion_light")

        assert result["automation_id"] == "automation.motion_light"
        assert result["trace_count"] == 1

    async def test_detail_mode_uses_trace_get(self):
        calls = []

        async def fake_call_ws(message_type, **kwargs):
            calls.append(message_type)
            if message_type == "config/entity_registry/get":
                return {"unique_id": "uid-1"}
            if message_type == "trace/get":
                return {
                    "trace": {"trigger": {}},
                    "config": {},
                    "context": {},
                    "timestamp": {"start": "2026-01-01T00:00:00Z"},
                    "state": "stopped",
                }
            return {}

        with patch("app.api.traces.call_ws", new=AsyncMock(side_effect=fake_call_ws)):
            result = await get_automation_traces_data(
                "automation.motion_light", run_id="1705312800.123456"
            )

        assert "trace/get" in calls
        assert result["run_id"] == "1705312800.123456"

    async def test_empty_listing_includes_diagnostics(self):
        async def fake_call_ws(message_type, **kwargs):
            if message_type == "config/entity_registry/get":
                return {"unique_id": "uid-1"}
            return []

        with patch("app.api.traces.call_ws", new=AsyncMock(side_effect=fake_call_ws)):
            with patch(
                "app.api.traces.get_entity_state",
                new=AsyncMock(return_value={"state": "on", "attributes": {}}),
            ):
                result = await get_automation_traces_data("automation.motion_light")

        assert result["trace_count"] == 0
        assert "diagnostics" in result

    async def test_unique_id_lookup_failure_falls_back_to_object_id(self):
        seen = {}

        async def fake_call_ws(message_type, **kwargs):
            if message_type == "config/entity_registry/get":
                raise HassWebSocketError("not found")
            if message_type == "trace/list":
                seen["item_id"] = kwargs.get("item_id")
                return [{"run_id": "1", "timestamp": {}, "state": "stopped"}]
            return {}

        with patch("app.api.traces.call_ws", new=AsyncMock(side_effect=fake_call_ws)):
            await get_automation_traces_data("automation.motion_light")

        assert seen["item_id"] == "motion_light"


class TestTraceFormatting:
    """The helpers that turn HA's flat trace paths into a readable result."""

    def test_steps_are_grouped_by_category(self):
        from app.api.traces import _classify_trace_steps

        raw = {
            "trigger/0": [{"timestamp": "t1"}],
            "condition/0": [{"timestamp": "t2", "result": {"result": True}}],
            "action/0": [{"timestamp": "t3"}],
            "action/1": [{"timestamp": "t4"}],
            "unrelated": "not a list",
        }
        triggers, conditions, actions = _classify_trace_steps(raw, "automation")

        assert len(triggers) == 1
        assert len(conditions) == 1
        assert len(actions) == 2
        # each step keeps the path it came from
        assert {a["path"] for a in actions} == {"action/0", "action/1"}

    def test_steps_are_sorted_by_timestamp(self):
        from app.api.traces import _classify_trace_steps

        raw = {"action/1": [{"timestamp": "2026-01-02"}], "action/0": [{"timestamp": "2026-01-01"}]}
        _, _, actions = _classify_trace_steps(raw, "automation")

        assert [a["timestamp"] for a in actions] == ["2026-01-01", "2026-01-02"]

    def test_condition_failure_keeps_its_error(self):
        """Dropping the error would hide why a condition evaluated falsy."""
        from app.api.traces import _populate_condition_results

        result: dict = {}
        _populate_condition_results(
            result,
            [
                {
                    "result": {"result": False},
                    "path": "condition/0",
                    "timestamp": "t1",
                    "error": "template error: undefined",
                }
            ],
        )

        entry = result["condition_results"][0]
        assert entry["result"] is False
        assert entry["error"] == "template error: undefined"
        assert entry["path"] == "condition/0"

    def test_no_conditions_adds_no_key(self):
        from app.api.traces import _populate_condition_results

        result: dict = {}
        _populate_condition_results(result, [])

        assert "condition_results" not in result

    def test_repeated_variables_are_deduplicated(self):
        from app.api.traces import _select_action_variables

        variables = {"repeat": {"index": 1, "first": True}}
        first, fingerprint = _select_action_variables(variables, True, None)
        second, _ = _select_action_variables(variables, True, fingerprint)

        assert first == variables
        assert second is None

    def test_deduplication_can_be_disabled(self):
        from app.api.traces import _select_action_variables

        variables = {"repeat": {"index": 1}}
        _, fingerprint = _select_action_variables(variables, True, None)
        again, _ = _select_action_variables(variables, False, fingerprint)

        assert again == variables

    def test_trigger_variables_are_skipped(self):
        """Trigger data is reported under its own key, not per action step."""
        from app.api.traces import _select_action_variables

        selected, _ = _select_action_variables({"trigger": {"entity_id": "light.k"}}, True, None)

        assert selected is None

    def test_none_values_are_dropped(self):
        from app.api.traces import _select_action_variables

        selected, _ = _select_action_variables({"a": 1, "b": None}, True, None)

        assert selected == {"a": 1}

    def test_all_none_yields_nothing(self):
        from app.api.traces import _select_action_variables

        selected, _ = _select_action_variables({"a": None}, True, None)

        assert selected is None

    def test_sections_filter_keeps_only_requested_keys(self):
        from app.api.traces import _filter_trace_sections

        result = {
            "automation_id": "automation.x",
            "run_id": "1",
            "trigger": {"a": 1},
            "condition_results": [],
            "action_trace": [],
            "config_summary": {},
        }
        filtered = _filter_trace_sections(dict(result), "trigger,actions")

        assert "trigger" in filtered
        assert "action_trace" in filtered
        assert "condition_results" not in filtered
        assert "config_summary" not in filtered
        # identity fields always survive
        assert filtered["automation_id"] == "automation.x"
        assert filtered["run_id"] == "1"
