"""Access policy: read-only mode, entity allowlist and control denylist.

Ported from the paultanger/ha-mcp-server fork (author: Paul Tanger), with one
deliberate change: **every control is off by default here.**

That fork is read-only out of the box and its allowlist is fail-closed — an
empty allowlist denies everything. Those are good defaults for the deployment it
was written for, but they would silently break every existing user of this
project on upgrade. So the mechanism is implemented in full and the posture is
inverted:

============================  ==========================  ======================
Control                       This project (default)      That fork's default
============================  ==========================  ======================
Writes                        allowed                     denied (read-only)
Empty entity allowlist        allows everything           denies everything
Empty control denylist        denies nothing              denies nothing
============================  ==========================  ======================

Configure ``policy.read_only``, ``policy.entity_allowlist`` and
``policy.control_denylist`` to adopt the stricter posture; the fork's proposed
configuration is written out in ``config/hass-mcp.example.json``.

Both lists accept exact entity IDs or fnmatch globs, e.g. ``sensor.gpu_*`` or
``lock.*``.

**Coverage.** Read-only mode is enforced on the shared HTTP client
(``app.core.client``), so it applies to every outgoing Home Assistant request
regardless of which call site built it. The allowlist and denylist are enforced
per entity at the points where an entity is named, which requires knowing the
entity ID: ``get_automation_config`` and ``update_automation`` address HA's
numeric automation *config* ID rather than an entity ID, so those two are
covered by read-only mode but not by the entity lists.

**Policy is fixed for the lifetime of the process.** Reads are filtered inside
cached functions, so a cached result reflects the policy in force when it was
computed. Settings are resolved when ``app.config`` is imported and are not
expected to change at runtime; changing them means restarting the server (or
clearing the cache, which tests do).
"""

import fnmatch
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PolicyViolation(Exception):
    """Raised when an operation is refused by policy."""


def _patterns(section: str) -> list[str]:
    """Read a pattern list from configuration at call time."""
    from app import config  # noqa: PLC0415 - lazy: config imports nothing from here

    return getattr(config, section, [])


def read_only() -> bool:
    """
    Report whether write operations are refused.

    Returns:
        True when the server is in read-only mode
    """
    from app import config  # noqa: PLC0415

    return bool(config.POLICY_READ_ONLY)


def is_allowed(entity_id: str) -> bool:
    """
    Check an entity against the allowlist.

    Unlike the fork this came from, an **empty allowlist allows everything**:
    configuring nothing must not break an existing deployment. Once any pattern
    is configured the list becomes authoritative and anything unmatched is
    denied.

    Args:
        entity_id: The entity ID to check

    Returns:
        True if the entity may be read or acted on

    Examples:
        >>> is_allowed("light.kitchen")  # no allowlist configured
        True
    """
    allowlist = _patterns("POLICY_ENTITY_ALLOWLIST")
    if not allowlist:
        return True
    if not entity_id:
        return False
    return any(fnmatch.fnmatchcase(entity_id, pattern) for pattern in allowlist)


def control_denied(entity_id: str) -> bool:
    """
    Check whether controlling an entity is refused.

    An entity is denied when the server is read-only, when it fails the
    allowlist, or when it matches the control denylist. The denylist is
    fail-open: configuring nothing denies nothing.

    Args:
        entity_id: The entity ID to check

    Returns:
        True if control of this entity is refused

    Examples:
        >>> control_denied("lock.front_door")  # nothing configured
        False
    """
    if read_only():
        return True
    if not is_allowed(entity_id):
        return True
    if not entity_id:
        return False
    return any(
        fnmatch.fnmatchcase(entity_id, pattern) for pattern in _patterns("POLICY_CONTROL_DENYLIST")
    )


def denied_reason(entity_id: str, *, control: bool = False) -> str:
    """
    Explain why an operation was refused, for the error returned to the caller.

    Args:
        entity_id: The entity that was refused
        control: True when the refusal was for a control action

    Returns:
        A human-readable reason
    """
    if control and read_only():
        return (
            "Refused: the server is in read-only mode. "
            "Set policy.read_only to false (or HASS_MCP_READ_ONLY=false) to allow writes."
        )
    if not is_allowed(entity_id):
        return (
            f"Refused: {entity_id} is not in the configured entity allowlist "
            "(policy.entity_allowlist)."
        )
    return (
        f"Refused: {entity_id} is in the control denylist (policy.control_denylist), "
        "so it can be read but never controlled."
    )


def denied(entity_id: str, *, control: bool = False) -> dict[str, Any]:
    """
    Build the error envelope returned when policy refuses an operation.

    Args:
        entity_id: The entity that was refused
        control: True when the refusal was for a control action

    Returns:
        A dictionary with an "error" key, matching the project's error contract
    """
    reason = denied_reason(entity_id, control=control)
    logger.warning(reason)
    return {"error": reason}


def qualify(domain: str, raw_id: str) -> str:
    """
    Build a full entity ID from a bare object ID.

    Several API functions take an identifier "without the domain prefix"
    (``morning_lights`` rather than ``automation.morning_lights``), while
    allowlist and denylist patterns are written against full entity IDs.

    Args:
        domain: The entity domain, e.g. "automation" or "script"
        raw_id: An object ID, or an already-qualified entity ID

    Returns:
        The qualified entity ID

    Examples:
        >>> qualify("automation", "morning_lights")
        'automation.morning_lights'
        >>> qualify("automation", "automation.morning_lights")
        'automation.morning_lights'
    """
    if raw_id.startswith(f"{domain}."):
        return raw_id
    return f"{domain}.{raw_id}"


def check_read(entity_id: str) -> dict[str, Any] | None:
    """
    Guard a read of one entity.

    Args:
        entity_id: The entity being read

    Returns:
        An error envelope when the read is refused, otherwise None

    Examples:
        if (refusal := check_read(entity_id)) is not None:
            return refusal
    """
    if is_allowed(entity_id):
        return None
    return denied(entity_id)


def check_control(entity_id: str) -> dict[str, Any] | None:
    """
    Guard a control action on one entity.

    Args:
        entity_id: The entity being acted on

    Returns:
        An error envelope when the action is refused, otherwise None

    Examples:
        if (refusal := check_control(qualify("script", script_id))) is not None:
            return refusal
    """
    if not control_denied(entity_id):
        return None
    return denied(entity_id, control=True)


def filter_entities(entities: Any) -> Any:
    """
    Drop entities that fail the allowlist from a list of entity dictionaries.

    A no-op when no allowlist is configured. Error envelopes pass through
    untouched so existing caller error handling keeps working.

    Args:
        entities: A list of entity dictionaries, or an error envelope

    Returns:
        The filtered list, or the input unchanged

    Examples:
        filter_entities([{"entity_id": "light.kitchen"}])
    """
    if not _patterns("POLICY_ENTITY_ALLOWLIST"):
        return entities
    if isinstance(entities, dict):  # error envelope
        return entities
    if not isinstance(entities, list):
        return entities
    if len(entities) == 1 and isinstance(entities[0], dict) and "error" in entities[0]:
        return entities
    return [
        entity
        for entity in entities
        if isinstance(entity, dict) and is_allowed(entity.get("entity_id", ""))
    ]


# HTTP methods that change Home Assistant state.
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Endpoints reached with a write method that are semantically reads: rendering a
# template and validating configuration change nothing, and blocking them in
# read-only mode would break diagnostics for no benefit.
READ_ONLY_SAFE_PATHS = (
    "/api/template",
    "/api/config/core/check_config",
)


def request_denied(method: str, path: str) -> str | None:
    """
    Decide whether an outgoing Home Assistant request is refused by policy.

    Enforced on the shared HTTP client, so it covers every call site rather
    than only the ones that route through ``call_service``. Home Assistant's
    REST API expresses some reads as POSTs; those are allowlisted.

    Args:
        method: The HTTP method
        path: The request path, e.g. "/api/services/light/turn_on"

    Returns:
        A reason string when the request must be refused, otherwise None

    Examples:
        >>> request_denied("GET", "/api/states") is None
        True
    """
    if method.upper() not in WRITE_METHODS:
        return None
    if not read_only():
        return None
    if any(path.startswith(safe) for safe in READ_ONLY_SAFE_PATHS):
        return None
    return (
        f"Refused: the server is in read-only mode, so {method.upper()} {path} "
        "is not allowed. Set policy.read_only to false "
        "(or HASS_MCP_READ_ONLY=false) to allow writes."
    )


def describe() -> dict[str, Any]:
    """
    Summarise the active policy, for startup logging and diagnostics.

    Returns:
        Dictionary with read_only, allowlist_patterns and denylist_patterns
    """
    return {
        "read_only": read_only(),
        "allowlist_patterns": len(_patterns("POLICY_ENTITY_ALLOWLIST")),
        "denylist_patterns": len(_patterns("POLICY_CONTROL_DENYLIST")),
    }
