"""Read-only diagnostic MCP tools ported from the nnion/hass-mcp fork.

Author of the originals: Nils Nilsson, who adapted them from
homeassistant-ai/ha-mcp.

All three are off by default — they answer narrow operational questions that
most deployments will not ask, and every exposed tool costs tokens in each
client's tool list. Enable them via ``tools.enabled``.
"""

import logging
from typing import Any

from app.api.traces import get_automation_traces_data
from app.api.updates import get_available_updates, get_hacs_info

logger = logging.getLogger(__name__)


async def get_available_updates_tool() -> dict[str, Any]:
    """
    List Home Assistant components with a pending update.

    Reads the standard update entity domain, which core, add-ons, HACS
    repositories and devices with firmware updates all publish — the same data
    Settings > System > Updates shows.

    Returns:
        Dictionary with count and updates. Each update carries entity_id,
        title, installed_version, latest_version, release_url and in_progress.
        An empty list means everything is current.

    Examples:
        get_available_updates() - What can be updated right now?

    Best Practices:
        - Read-only; installing an update is a separate service call
        - Check release_url before applying a core update
    """
    logger.info("Listing available updates")
    return await get_available_updates()


async def get_hacs_info_tool(
    query: str | None = None,
    category: str | None = None,
    installed_only: bool = True,
    limit: int = 20,
) -> dict[str, Any]:
    """
    List HACS (Home Assistant Community Store) repositories.

    Requires HACS to be installed; an error is returned otherwise.

    Args:
        query: Case-insensitive filter over name, full_name and description
        category: One of "integration", "lovelace", "theme", "appdaemon",
                  "python_script" or "template"
        installed_only: Only installed repositories (default: True); set False
                        to search the whole store
        limit: Maximum repositories to return (default: 20)

    Returns:
        Dictionary with count, total_matches and repositories

    Examples:
        get_hacs_info() - Installed HACS repositories
        get_hacs_info(query="mushroom", installed_only=False) - Search the store
        get_hacs_info(category="lovelace") - Installed frontend plugins

    Best Practices:
        - Keep installed_only=True unless the user is looking for something new
        - total_matches shows how many matched before limit was applied
    """
    logger.info(f"Listing HACS repositories: query={query}, category={category}")
    return await get_hacs_info(query, category, installed_only, limit)


async def get_automation_traces_tool(
    automation_id: str,
    run_id: str | None = None,
    limit: int = 10,
    offset: int = 0,
    order: str = "newest",
    deduplicate: bool = True,
    detailed: bool = False,
    sections: str | None = None,
) -> dict[str, Any]:
    """
    Get execution traces for an automation or script, to debug what it actually did.

    Traces show what triggered a run, which conditions passed or failed, which
    actions ran and with what results, and the variable values along the way.
    This is richer than get_automation_execution_log, which reads the logbook
    and only reports that a run happened.

    Args:
        automation_id: Full entity ID, e.g. "automation.motion_light" or
                       "script.morning_routine"
        run_id: A specific trace to fetch in full detail. Omit to list recent runs
        limit: Maximum traces when listing (1-50, default: 10)
        offset: Traces to skip, for paging when has_more is true
        order: "newest" (default) or "oldest"
        deduplicate: Omit an action step's variables when identical to the
                     previous step's (default: True)
        detailed: Also include logbook entries and context metadata
        sections: Comma-separated subset to return in detail mode:
                  trigger, conditions, actions, config, error, logbook, context

    Returns:
        Listing mode: automation_id, trace_count, total_available, offset,
        order, has_more, traces, and diagnostics when nothing was found.
        Detail mode: trigger, condition_results, action_trace, config_summary,
        plus error, logbook_entries and context when present.

    Examples:
        automation_id="automation.motion_light" - List recent runs
        automation_id="automation.motion_light", run_id="1705312800.123456" - One run in detail
        automation_id="script.morning_routine", detailed=True - With logbook context

    Best Practices:
        - Not triggering at all: list traces first; none means it never ran,
          and diagnostics explains why
        - Runs but does nothing: fetch a detailed trace and look for a false
          entry in condition_results
        - Wrong branch taken: check action_trace for choose branch selection
          and per-step errors
    """
    logger.info(f"Getting traces for {automation_id} (run_id={run_id})")
    return await get_automation_traces_data(
        automation_id,
        run_id=run_id,
        limit=limit,
        offset=offset,
        order=order,
        deduplicate=deduplicate,
        detailed=detailed,
        sections=sections,
    )
