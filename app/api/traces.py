"""Automation and script execution traces.

Ported from the nnion/hass-mcp fork (author: Nils Nilsson), which in turn
adapted the concept from homeassistant-ai/ha-mcp.

Traces show what actually happened during a run: what triggered it, which
conditions passed or failed, which actions ran and with what results, and the
variable values along the way. Backed by Home Assistant's ``trace/list`` and
``trace/get`` WebSocket commands — the same data the frontend's "Traces" tab
shows, and not available over REST.

This complements ``get_automation_execution_log``, which reads the logbook and
so only reports *that* an automation ran, not what happened inside it.
"""

import json
import logging
from typing import Any

from app.api.entities import get_entity_state
from app.core import policy
from app.core.decorators import handle_api_errors
from app.core.ws import call_ws

logger = logging.getLogger(__name__)

_TRACE_SECTION_KEYS = {
    "trigger": "trigger",
    "conditions": "condition_results",
    "actions": "action_trace",
    "config": "config_summary",
    "error": "error",
    "logbook": "logbook_entries",
    "context": "context",
}


async def _resolve_trace_item_id(automation_id: str, fallback_object_id: str) -> str:
    """Resolve entity_id to the unique_id HA stores traces under.

    HA keys trace storage by the automation/script's unique_id, not its
    entity_id. Falls back to the object_id (entity_id suffix) on any
    lookup failure — the subsequent trace/list or trace/get call then
    surfaces HA's own "not found" error instead of failing silently here.
    """
    try:
        result = await call_ws("config/entity_registry/get", entity_id=automation_id)
        unique_id = (result or {}).get("unique_id")
        if unique_id:
            return str(unique_id)
    except Exception as e:
        logger.debug(f"Could not resolve unique_id for {automation_id}: {e}")
    return fallback_object_id


async def _gather_trace_diagnostics(automation_id: str, domain: str) -> dict[str, Any]:
    """Explain why no traces were found: disabled, never triggered, or storage off."""
    diagnostics: dict[str, Any] = {
        "automation_exists": False,
        "automation_enabled": False,
        "trace_storage_enabled": True,
        "last_triggered": None,
        "suggestion": "",
    }
    try:
        entity_state = await get_entity_state(automation_id)
        if entity_state and "error" not in entity_state:
            diagnostics["automation_exists"] = True
            diagnostics["automation_enabled"] = entity_state.get("state") == "on"
            attributes = entity_state.get("attributes", {})
            diagnostics["last_triggered"] = attributes.get("last_triggered")

            if domain == "automation":
                try:
                    config = await call_ws("automation/config", entity_id=automation_id)
                    stored_traces = (config or {}).get("stored_traces")
                    if stored_traces is not None and stored_traces <= 0:
                        diagnostics["trace_storage_enabled"] = False
                except Exception as e:
                    # Best-effort diagnostic; the caller still gets the rest.
                    logger.debug(f"Could not read automation config for {automation_id}: {e}")

            if not diagnostics["automation_enabled"]:
                diagnostics["suggestion"] = (
                    f"The {domain} is currently disabled (state: off). "
                    "Enable it to start recording traces."
                )
            elif diagnostics["last_triggered"] is None:
                diagnostics["suggestion"] = (
                    f"The {domain} has never been triggered. "
                    "Wait for it to trigger or trigger it manually to generate traces."
                )
            elif not diagnostics["trace_storage_enabled"]:
                diagnostics["suggestion"] = (
                    "Trace storage is disabled for this automation. "
                    "Set 'stored_traces' to a positive number in the automation config."
                )
            else:
                diagnostics["suggestion"] = (
                    "Traces may have been cleared or expired — "
                    "Home Assistant only keeps a limited number of recent traces."
                )
        else:
            diagnostics["suggestion"] = (
                f"Could not find {automation_id}. Verify the entity_id is correct."
            )
    except Exception as e:
        diagnostics["suggestion"] = f"Could not find {automation_id}: {e}"
    return diagnostics


def _categorize_trace_path(path: str, domain: str) -> str | None:
    """Map a trace path key (e.g. 'action/0/1') to trigger/condition/action."""
    if path == "trigger" or path.startswith("trigger/"):
        return "trigger"
    if path == "condition" or path.startswith("condition/"):
        return "condition"
    if path == "action" or path.startswith(("action/", "sequence/")):
        return "action"
    if domain == "script" and path.split("/", maxsplit=1)[0].isdigit():
        return "action"
    return None


def _classify_trace_steps(
    raw_trace: dict[str, Any], domain: str
) -> "tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]":
    """Group flat trace path entries into sorted trigger/condition/action lists."""
    buckets: dict[str, list[dict[str, Any]]] = {"trigger": [], "condition": [], "action": []}

    for path, steps in raw_trace.items():
        if not isinstance(steps, list):
            continue
        category = _categorize_trace_path(path, domain)
        if category is None:
            continue
        for step in steps:
            step_info = dict(step)
            step_info["path"] = path
            buckets[category].append(step_info)

    for steps_list in buckets.values():
        steps_list.sort(key=lambda item: (item.get("timestamp", ""), item.get("path", "")))

    return buckets["trigger"], buckets["condition"], buckets["action"]


def _select_action_variables(
    variables: dict[str, Any],
    deduplicate: bool,
    last_fingerprint: str | None,
) -> "tuple[dict[str, Any] | None, str | None]":
    """Pick variables to record for an action step, deduplicating vs. the previous step."""
    if not variables or "trigger" in variables:
        return None, last_fingerprint
    useful_vars = {k: v for k, v in variables.items() if v is not None}
    if not useful_vars:
        return None, last_fingerprint
    if not deduplicate:
        return useful_vars, last_fingerprint
    try:
        fingerprint = json.dumps(useful_vars, sort_keys=True, default=str)
    except (TypeError, ValueError):
        fingerprint = str(useful_vars)
    if fingerprint == last_fingerprint:
        return None, last_fingerprint
    return useful_vars, fingerprint


def _populate_trigger_info(
    result: dict[str, Any], triggers: list[dict[str, Any]], trace: dict[str, Any]
) -> None:
    if triggers:
        trigger_step = triggers[0]
        trigger_vars = trigger_step.get("changed_variables", {}).get("trigger", {})
        if not trigger_vars:
            trigger_vars = trigger_step.get("variables", {}).get("trigger", {})
        result["trigger"] = {
            "platform": trigger_vars.get("platform"),
            "description": trigger_vars.get("description"),
        }
        if "to_state" in trigger_vars:
            result["trigger"]["to_state"] = trigger_vars.get("to_state", {}).get("state")
        if "from_state" in trigger_vars:
            result["trigger"]["from_state"] = trigger_vars.get("from_state", {}).get("state")
        if "entity_id" in trigger_vars:
            result["trigger"]["entity_id"] = trigger_vars["entity_id"]
        if "error" in trigger_step:
            result["trigger"]["error"] = trigger_step["error"]
    if "trigger" not in result and "trigger" in trace:
        result["trigger"] = {"description": trace["trigger"]}


def _populate_condition_results(result: dict[str, Any], conditions: list[dict[str, Any]]) -> None:
    if conditions:
        condition_results = []
        for cond in conditions:
            cond_result = {
                "result": cond.get("result", {}).get("result"),
                "path": cond.get("path"),
            }
            if "timestamp" in cond:
                cond_result["timestamp"] = cond["timestamp"]
            # HA always records the template error on the failing step;
            # dropping this would hide *why* a condition evaluated falsy.
            if "error" in cond:
                cond_result["error"] = cond["error"]
            condition_results.append(cond_result)
        result["condition_results"] = condition_results


def _populate_action_trace(
    result: dict[str, Any], actions: list[dict[str, Any]], deduplicate: bool
) -> None:
    if not actions:
        return
    action_results = []
    last_vars_fingerprint: str | None = None
    for action in actions:
        action_info: dict[str, Any] = {"path": action.get("path")}
        if "timestamp" in action:
            action_info["timestamp"] = action["timestamp"]
        action_result = action.get("result", {})
        if action_result:
            action_info["result"] = action_result
        if "error" in action:
            action_info["error"] = action["error"]
        variables = action.get("variables") or action.get("changed_variables", {})
        useful_vars, last_vars_fingerprint = _select_action_variables(
            variables, deduplicate, last_vars_fingerprint
        )
        if useful_vars is not None:
            action_info["variables"] = useful_vars
        if "child_id" in action:
            action_info["child_id"] = action["child_id"]
        action_results.append(action_info)
    result["action_trace"] = action_results


def _filter_trace_sections(result: dict[str, Any], sections: str) -> dict[str, Any]:
    requested = {s.strip().lower() for s in sections.split(",")}
    keep_keys = {_TRACE_SECTION_KEYS[s] for s in requested if s in _TRACE_SECTION_KEYS}
    keep_keys |= {"success", "automation_id", "run_id", "timestamp", "state", "script_execution"}
    return {k: v for k, v in result.items() if k in keep_keys}


def _format_detailed_trace(
    automation_id: str,
    run_id: str,
    trace: dict[str, Any],
    *,
    deduplicate: bool = True,
    detailed: bool = False,
    sections: str | None = None,
) -> dict[str, Any]:
    domain = "automation" if automation_id.startswith("automation.") else "script"
    result: dict[str, Any] = {
        "automation_id": automation_id,
        "run_id": run_id,
        "timestamp": trace.get("timestamp"),
        "state": trace.get("state"),
    }

    raw_trace = trace.get("trace", {})
    triggers, conditions, actions = _classify_trace_steps(raw_trace, domain)
    _populate_trigger_info(result, triggers, trace)
    _populate_condition_results(result, conditions)
    _populate_action_trace(result, actions, deduplicate)

    config = trace.get("config", {})
    if config:
        result["config_summary"] = {
            "alias": config.get("alias"),
            "mode": config.get("mode", "single"),
        }

    if trace.get("error"):
        result["error"] = trace["error"]
    if trace.get("script_execution"):
        result["script_execution"] = trace["script_execution"]

    if detailed:
        if "logbook_entries" in trace:
            result["logbook_entries"] = trace["logbook_entries"]
        if trace.get("context"):
            result["context"] = trace["context"]

    if sections:
        result = _filter_trace_sections(result, sections)

    return result


def _format_trace_list(
    automation_id: str,
    traces: list[dict[str, Any]],
    limit: int,
    diagnostics: dict[str, Any] | None = None,
    *,
    offset: int = 0,
    order: str = "newest",
) -> dict[str, Any]:
    # HA's trace/list returns traces oldest-first. Pick a window from the
    # end for newest-first, or from the start for oldest-first.
    if order == "newest":
        end = len(traces) - offset
        start = max(end - limit, 0)
        window = list(reversed(traces[start:end])) if end > 0 else []
    else:
        window = traces[offset : offset + limit]

    formatted_traces = []
    for trace in window:
        trace_info: dict[str, Any] = {
            "run_id": trace.get("run_id"),
            "timestamp": trace.get("timestamp"),
            "state": trace.get("state"),
        }
        if trace.get("trigger"):
            trace_info["trigger"] = trace["trigger"]
        if trace.get("error"):
            trace_info["error"] = trace["error"]
        if "script_execution" in trace:
            trace_info["execution"] = trace.get("script_execution")
        formatted_traces.append(trace_info)

    result: dict[str, Any] = {
        "automation_id": automation_id,
        "trace_count": len(formatted_traces),
        "total_available": len(traces),
        "offset": offset,
        "order": order,
        "has_more": offset + len(formatted_traces) < len(traces),
        "traces": formatted_traces,
        "hint": "Use run_id with this tool to get detailed trace information",
    }
    if diagnostics is not None and len(traces) == 0:
        result["diagnostics"] = diagnostics
    return result


@handle_api_errors
async def get_automation_traces_data(
    automation_id: str,
    run_id: str | None = None,
    limit: int = 10,
    offset: int = 0,
    order: str = "newest",
    deduplicate: bool = True,
    detailed: bool = False,
    sections: str | None = None,
) -> dict[str, Any]:
    """Retrieve execution traces for an automation or script.

    Traces show what happened during a run: what triggered it, which
    conditions passed/failed, what actions ran (with results/errors), and
    variable values along the way. Backed by HA's `trace/list` and
    `trace/get` WebSocket commands — the same data the HA frontend's
    "Traces" tab shows.

    Args:
        automation_id: Full entity_id, e.g. `automation.motion_light` or
                       `script.morning_routine`.
        run_id: Specific trace to fetch in full detail. Omit to list
                recent traces instead.
        limit: Max traces to return when listing (1-50, default 10).
        offset: Traces to skip from the start of the requested `order` —
                use with `limit` to page through stored traces when
                `has_more` is true.
        order: `newest` (default, most-recent first) or `oldest`
               (chronological first).
        deduplicate: In detail mode, omit an action step's variables if
                     identical to the previous step's (default True).
                     Set False for full variables at every step.
        detailed: In detail mode, also include logbook entries and
                  context metadata (default False) — use when the
                  standard trace lacks enough detail to debug.
        sections: In detail mode, comma-separated subset to return:
                  `trigger,conditions,actions,config,error,logbook,
                  context`. Omit for everything.

    Returns:
        Listing mode: `automation_id`, `trace_count`, `total_available`,
        `offset`, `order`, `has_more`, `traces` (run summaries), and
        `diagnostics` (why enabled/last_triggered/trace-storage state
        might explain an empty list) when none were found.

        Detail mode: `automation_id`, `run_id`, `timestamp`, `state`,
        `trigger`, `condition_results`, `action_trace`, `config_summary`,
        plus `error`/`logbook_entries`/`context` when present.

    Examples:
        get_automation_traces_data("automation.motion_light")
        get_automation_traces_data("automation.motion_light", run_id="1705312800.123456")
        get_automation_traces_data("automation.motion_light", run_id="...", detailed=True)

    Debugging tips:
        - Not triggering at all: list traces first — none means it never
          ran; check `diagnostics` for why.
        - Runs but does nothing: get a detailed trace and check
          `condition_results` for a `false`.
        - Wrong branch / unexpected action: get a detailed trace and
          check `action_trace` for `choose` branch selection and
          per-step `error`.
    """
    # Policy: refuse when the entity is outside the configured lists.
    if (refusal := policy.check_read(automation_id)) is not None:
        return refusal

    if automation_id.startswith("automation."):
        domain = "automation"
    elif automation_id.startswith("script."):
        domain = "script"
    else:
        raise ValueError(
            f"Invalid entity_id {automation_id!r} — must start with 'automation.' or 'script.'"
        )
    if order not in ("newest", "oldest"):
        raise ValueError(f"order must be 'newest' or 'oldest', got {order!r}")

    object_id = automation_id.split(".", 1)[1]
    item_id = await _resolve_trace_item_id(automation_id, object_id)
    limit = max(1, min(limit, 50))

    if run_id:
        trace_data = await call_ws("trace/get", domain=domain, item_id=item_id, run_id=run_id)
        return _format_detailed_trace(
            automation_id,
            run_id,
            trace_data or {},
            deduplicate=deduplicate,
            detailed=detailed,
            sections=sections,
        )

    traces_data = await call_ws("trace/list", domain=domain, item_id=item_id) or []

    if not traces_data:
        diagnostics = await _gather_trace_diagnostics(automation_id, domain)
        return _format_trace_list(
            automation_id, traces_data, limit, diagnostics, offset=offset, order=order
        )

    return _format_trace_list(automation_id, traces_data, limit, offset=offset, order=order)
