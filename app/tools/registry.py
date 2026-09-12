"""Registry of every MCP tool this server can expose.

The tool surface is configurable. This module is the single declarative list of
what *can* be exposed; ``app.config`` decides what *is*, and ``app.server``
registers accordingly.

Why a subset is exposed by default: consolidating 92 original tools into the
unified ones left many superseded functions in the tree. They still work, but
exposing all 114 would bloat every client's tool list (and its token budget)
with near-duplicates. 35 are on by default — the unified tools plus the
specialised ones they do not cover.

Enabling a non-default tool is supported: add its name to ``tools.enabled`` in
the configuration file, or to ``MCP_TOOLS_ENABLED``. See
``config/hass-mcp.example.json`` for the full list with defaults marked.
"""

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast


@dataclass(frozen=True)
class ToolSpec:
    """One tool that can be registered with the MCP server.

    Attributes:
        name: The tool name exposed to MCP clients, and the function's name
        module: The module under ``app.tools`` holding it
        default: Whether it is exposed unless configuration says otherwise
        label: The command label used for execution logging
    """

    name: str
    module: str
    default: bool
    label: str = field(default="")

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", self.name)

    def load(self) -> Callable:
        """
        Import and return the underlying tool function.

        Returns:
            The callable to register with the MCP server

        Raises:
            AttributeError: If the module does not define the expected function
        """
        module = importlib.import_module(f"app.tools.{self.module}")
        return cast(Callable, getattr(module, self.name))


# Every tool this server knows how to expose.
TOOL_SPECS: tuple[ToolSpec, ...] = (
    # --- areas ---
    ToolSpec("create_area_tool", "areas", False),
    ToolSpec("delete_area_tool", "areas", False),
    ToolSpec("get_area_entities_tool", "areas", False),
    ToolSpec("get_area_summary_tool", "areas", False),
    ToolSpec("list_areas_tool", "areas", False),
    ToolSpec("update_area_tool", "areas", False),
    # --- automations ---
    ToolSpec("create_automation_tool", "automations", False),
    ToolSpec("delete_automation_tool", "automations", False),
    ToolSpec("disable_automation_tool", "automations", False),
    ToolSpec("enable_automation_tool", "automations", False),
    ToolSpec("get_automation_config_tool", "automations", False),
    ToolSpec(
        "get_automation_execution_log_tool",
        "automations",
        True,
        label="get_automation_execution_log",
    ),
    ToolSpec("list_automations", "automations", False),
    ToolSpec("reload_automations_tool", "automations", False),
    ToolSpec("trigger_automation_tool", "automations", False),
    ToolSpec("update_automation_tool", "automations", False),
    ToolSpec(
        "validate_automation_config_tool", "automations", True, label="validate_automation_config"
    ),
    # --- backups ---
    ToolSpec("create_backup_tool", "backups", False),
    ToolSpec("delete_backup_tool", "backups", False),
    ToolSpec("list_backups_tool", "backups", False),
    ToolSpec("restore_backup_tool", "backups", True, label="restore_backup"),
    # --- blueprints ---
    ToolSpec(
        "create_automation_from_blueprint_tool",
        "blueprints",
        True,
        label="create_automation_from_blueprint",
    ),
    ToolSpec("get_blueprint_tool", "blueprints", False),
    ToolSpec("import_blueprint_tool", "blueprints", True, label="import_blueprint"),
    ToolSpec("list_blueprints_tool", "blueprints", False),
    # --- calendars ---
    ToolSpec("create_calendar_event_tool", "calendars", True, label="create_calendar_event"),
    ToolSpec("get_calendar_events_tool", "calendars", True, label="get_calendar_events"),
    ToolSpec("list_calendars_tool", "calendars", False),
    # --- devices ---
    ToolSpec("get_device_entities_tool", "devices", False),
    ToolSpec("get_device_stats_tool", "devices", False),
    ToolSpec("get_device_tool", "devices", False),
    ToolSpec("list_devices_tool", "devices", False),
    # --- diagnostics ---
    ToolSpec("analyze_automation_conflicts_tool", "diagnostics", False),
    ToolSpec("check_entity_dependencies_tool", "diagnostics", False),
    ToolSpec("diagnose_entity_tool", "diagnostics", False),
    ToolSpec("get_integration_errors_tool", "diagnostics", False),
    # --- entities ---
    ToolSpec("entity_action", "entities", True),
    ToolSpec("get_entity", "entities", True),
    ToolSpec("list_entities", "entities", False),
    ToolSpec("search_entities_tool", "entities", False),
    ToolSpec("semantic_search_entities_tool", "entities", False),
    # --- entity_descriptions ---
    ToolSpec("generate_entity_description_tool", "entity_descriptions", False),
    ToolSpec("generate_entity_descriptions_batch_tool", "entity_descriptions", False),
    # --- entity_suggestions ---
    ToolSpec(
        "get_entity_suggestions_tool", "entity_suggestions", True, label="get_entity_suggestions"
    ),
    # --- events ---
    ToolSpec("fire_event_tool", "events", False),
    ToolSpec("get_events_tool", "events", False),
    ToolSpec("list_event_types_tool", "events", False),
    # --- helpers ---
    ToolSpec("get_helper_tool", "helpers", False),
    ToolSpec("list_helpers_tool", "helpers", False),
    ToolSpec("update_helper_tool", "helpers", True, label="update_helper"),
    # --- integrations ---
    ToolSpec("get_integration_config_tool", "integrations", False),
    ToolSpec("list_integrations", "integrations", False),
    ToolSpec("reload_integration_tool", "integrations", True, label="reload_integration"),
    # --- logbook ---
    ToolSpec("get_entity_logbook_tool", "logbook", False),
    ToolSpec("get_logbook_tool", "logbook", False),
    ToolSpec("search_logbook_tool", "logbook", False),
    # --- notifications ---
    ToolSpec("list_notification_services_tool", "notifications", False),
    ToolSpec("send_notification_tool", "notifications", False),
    ToolSpec("test_notification_tool", "notifications", False),
    # --- diagnostics_extra ---
    ToolSpec("get_automation_traces_tool", "diagnostics_extra", False),
    ToolSpec("get_available_updates_tool", "diagnostics_extra", False),
    ToolSpec("get_hacs_info_tool", "diagnostics_extra", False),
    # --- raw ---
    ToolSpec("call_api", "raw", False),
    # --- query_processing ---
    ToolSpec("process_natural_language_query", "query_processing", True),
    # --- scenes ---
    ToolSpec("activate_scene_tool", "scenes", False),
    ToolSpec("create_scene_tool", "scenes", False),
    ToolSpec("get_scene_tool", "scenes", False),
    ToolSpec("list_scenes_tool", "scenes", False),
    ToolSpec("reload_scenes_tool", "scenes", False),
    # --- scripts ---
    ToolSpec("get_script_tool", "scripts", False),
    ToolSpec("list_scripts_tool", "scripts", False),
    ToolSpec("reload_scripts_tool", "scripts", False),
    ToolSpec("run_script_tool", "scripts", True, label="run_script"),
    # --- services ---
    ToolSpec("call_service_simple_tool", "services", True, label="call_service_simple"),
    ToolSpec("call_service_tool", "services", True, label="call_service"),
    ToolSpec("list_services_tool", "services", True, label="list_services"),
    # --- statistics ---
    ToolSpec("analyze_usage_patterns_tool", "statistics", False),
    ToolSpec("get_domain_statistics_tool", "statistics", False),
    ToolSpec("get_entity_statistics_tool", "statistics", False),
    # --- system ---
    ToolSpec("core_config", "system", False),
    ToolSpec("domain_summary_tool", "system", False),
    ToolSpec("get_cache_statistics_tool", "system", False),
    ToolSpec("get_error_log", "system", False),
    ToolSpec("get_history", "system", False),
    ToolSpec("get_version", "system", False),
    ToolSpec("restart_ha", "system", True),
    ToolSpec("system_health", "system", False),
    ToolSpec("system_overview", "system", False),
    # --- tags ---
    ToolSpec("create_tag_tool", "tags", False),
    ToolSpec("delete_tag_tool", "tags", False),
    ToolSpec("get_tag_automations_tool", "tags", True, label="get_tag_automations"),
    ToolSpec("list_tags_tool", "tags", False),
    # --- templates ---
    ToolSpec("test_template_tool", "templates", True, label="test_template"),
    # --- unified ---
    ToolSpec("diagnose", "unified", True),
    ToolSpec("generate_entity_description", "unified", True),
    ToolSpec("get_item", "unified", True),
    ToolSpec("get_item_entities", "unified", True),
    ToolSpec("get_item_summary", "unified", True),
    ToolSpec("get_logbook", "unified", True),
    ToolSpec("get_statistics", "unified", True),
    ToolSpec("get_system_data", "unified", True),
    ToolSpec("get_system_info", "unified", True),
    ToolSpec("list_items", "unified", True),
    ToolSpec("manage_events", "unified", True),
    ToolSpec("manage_item", "unified", True),
    ToolSpec("manage_notifications", "unified", True),
    ToolSpec("manage_webhooks", "unified", True),
    ToolSpec("search_entities", "unified", True),
    # --- webhooks ---
    ToolSpec("list_webhooks_tool", "webhooks", False),
    ToolSpec("test_webhook_tool", "webhooks", False),
    # --- zones ---
    ToolSpec("create_zone_tool", "zones", False),
    ToolSpec("delete_zone_tool", "zones", False),
    ToolSpec("list_zones_tool", "zones", False),
    ToolSpec("update_zone_tool", "zones", False),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}

ALL_TOOL_NAMES: frozenset[str] = frozenset(TOOLS_BY_NAME)

DEFAULT_ENABLED: frozenset[str] = frozenset(spec.name for spec in TOOL_SPECS if spec.default)


def resolve_enabled(
    enabled: list[str] | None = None,
    disabled: list[str] | None = None,
) -> tuple[list[ToolSpec], list[str]]:
    """
    Work out which tools to register.

    Args:
        enabled: Explicit allow-list. When None or empty the default set is
                 used; the literal entry "all" selects every known tool.
        disabled: Names to remove from whatever the allow-list produced

    Returns:
        A (specs, unknown_names) tuple. specs is in TOOL_SPECS order;
        unknown_names lists configured names that match no tool, so the caller
        can warn rather than fail.

    Examples:
        resolve_enabled()                      # the default surface
        resolve_enabled(["all"])               # everything
        resolve_enabled(None, ["restart_ha"])  # default minus one
    """
    unknown: list[str] = []

    if enabled:
        if any(name == "all" for name in enabled):
            selected = set(ALL_TOOL_NAMES)
        else:
            selected = set()
            for name in enabled:
                if name in ALL_TOOL_NAMES:
                    selected.add(name)
                else:
                    unknown.append(name)
    else:
        selected = set(DEFAULT_ENABLED)

    for name in disabled or []:
        if name in ALL_TOOL_NAMES:
            selected.discard(name)
        else:
            unknown.append(name)

    return [spec for spec in TOOL_SPECS if spec.name in selected], unknown
