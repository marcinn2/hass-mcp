"""Tests for the configurable MCP tool surface.

The registry is the declarative list of every tool that can be exposed;
configuration decides which of them actually are. The default set must keep
reproducing the historical surface so that enabling the feature changes nothing
for existing users.
"""

import json

import pytest

from app.tools.registry import (
    ALL_TOOL_NAMES,
    DEFAULT_ENABLED,
    TOOL_SPECS,
    TOOLS_BY_NAME,
    resolve_enabled,
)

# The tool surface as shipped before the surface became configurable.
HISTORICAL_SURFACE = {
    "get_entity",
    "entity_action",
    "search_entities",
    "get_entity_suggestions_tool",
    "process_natural_language_query",
    "generate_entity_description",
    "list_items",
    "get_item",
    "manage_item",
    "get_automation_execution_log_tool",
    "validate_automation_config_tool",
    "run_script_tool",
    "get_item_entities",
    "get_item_summary",
    "reload_integration_tool",
    "get_system_info",
    "get_system_data",
    "restart_ha",
    "call_service_tool",
    "call_service_simple_tool",
    "list_services_tool",
    "test_template_tool",
    "get_logbook",
    "get_statistics",
    "diagnose",
    "import_blueprint_tool",
    "create_automation_from_blueprint_tool",
    "manage_events",
    "manage_notifications",
    "get_calendar_events_tool",
    "create_calendar_event_tool",
    "update_helper_tool",
    "get_tag_automations_tool",
    "manage_webhooks",
    "restore_backup_tool",
}


class TestRegistryIntegrity:
    def test_names_are_unique(self):
        names = [spec.name for spec in TOOL_SPECS]
        assert len(names) == len(set(names))

    def test_every_spec_loads(self):
        """A registry entry naming a function that no longer exists is a bug."""
        for spec in TOOL_SPECS:
            assert callable(spec.load()), spec.name

    def test_label_defaults_to_the_name(self):
        for spec in TOOL_SPECS:
            assert spec.label

    def test_lookup_table_matches(self):
        assert set(TOOLS_BY_NAME) == ALL_TOOL_NAMES
        assert len(TOOLS_BY_NAME) == len(TOOL_SPECS)

    def test_default_is_a_subset_of_all(self):
        assert DEFAULT_ENABLED <= ALL_TOOL_NAMES

    def test_some_tools_are_off_by_default(self):
        """The whole point: more tools exist than are exposed."""
        assert len(DEFAULT_ENABLED) < len(ALL_TOOL_NAMES)


class TestDefaultSurfaceIsPreserved:
    def test_default_matches_the_historical_surface(self):
        assert DEFAULT_ENABLED == HISTORICAL_SURFACE

    async def test_server_exposes_the_default_set(self):
        from app import server

        live = {tool.name for tool in await server.mcp.list_tools()}
        assert live == HISTORICAL_SURFACE


class TestResolveEnabled:
    def test_no_configuration_gives_the_default_set(self):
        specs, unknown = resolve_enabled()
        assert {s.name for s in specs} == DEFAULT_ENABLED
        assert unknown == []

    def test_empty_lists_give_the_default_set(self):
        specs, unknown = resolve_enabled([], [])
        assert {s.name for s in specs} == DEFAULT_ENABLED
        assert unknown == []

    def test_all_selects_everything(self):
        specs, unknown = resolve_enabled(["all"])
        assert {s.name for s in specs} == ALL_TOOL_NAMES
        assert unknown == []

    def test_explicit_allow_list(self):
        specs, unknown = resolve_enabled(["get_entity", "list_items"])
        assert {s.name for s in specs} == {"get_entity", "list_items"}
        assert unknown == []

    def test_allow_list_can_enable_a_non_default_tool(self):
        off_by_default = next(s.name for s in TOOL_SPECS if not s.default)
        specs, unknown = resolve_enabled([off_by_default])
        assert {s.name for s in specs} == {off_by_default}
        assert unknown == []

    def test_disabled_trims_the_default_set(self):
        specs, unknown = resolve_enabled(None, ["restart_ha"])
        names = {s.name for s in specs}
        assert names == DEFAULT_ENABLED - {"restart_ha"}
        assert unknown == []

    def test_disabled_applies_to_all(self):
        specs, _ = resolve_enabled(["all"], ["restart_ha"])
        assert "restart_ha" not in {s.name for s in specs}
        assert len({s.name for s in specs}) == len(ALL_TOOL_NAMES) - 1

    def test_disabled_wins_over_enabled(self):
        specs, _ = resolve_enabled(["get_entity", "list_items"], ["list_items"])
        assert {s.name for s in specs} == {"get_entity"}

    def test_unknown_names_are_reported_not_raised(self):
        specs, unknown = resolve_enabled(["get_entity", "nope"], ["also_nope"])
        assert {s.name for s in specs} == {"get_entity"}
        assert sorted(unknown) == ["also_nope", "nope"]

    def test_result_follows_registry_order(self):
        specs, _ = resolve_enabled(["all"])
        assert [s.name for s in specs] == [s.name for s in TOOL_SPECS]

    def test_everything_disabled_yields_nothing(self):
        specs, unknown = resolve_enabled(["all"], sorted(ALL_TOOL_NAMES))
        assert specs == []
        assert unknown == []


class TestExampleConfigMatchesRegistry:
    """The shipped example must stay in step with the code."""

    @pytest.fixture
    def tools_section(self):
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "config" / "hass-mcp.example.json"
        return json.loads(path.read_text())["tools"]

    def test_enabled_list_is_exactly_the_default_set(self, tools_section):
        assert set(tools_section["enabled"]) == DEFAULT_ENABLED

    def test_disabled_list_is_empty(self, tools_section):
        assert tools_section["disabled"] == []

    def test_comment_documents_every_non_default_tool(self, tools_section):
        """Each tool that is off by default must be named in the comment."""
        comment = "\n".join(tools_section["$comment_disabled"])
        for spec in TOOL_SPECS:
            if not spec.default:
                assert spec.name in comment, spec.name

    def test_comment_states_the_correct_counts(self, tools_section):
        comment = "\n".join(tools_section["$comment_disabled"])
        off = len(ALL_TOOL_NAMES) - len(DEFAULT_ENABLED)
        assert f"{off} of {len(ALL_TOOL_NAMES)}" in comment
