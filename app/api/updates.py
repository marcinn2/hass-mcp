"""Pending Home Assistant updates and HACS repositories.

Ported from the nnion/hass-mcp fork (author: Nils Nilsson), which in turn
adapted these from homeassistant-ai/ha-mcp.

Both are read-only diagnostics:

- :func:`get_available_updates` reads the standard ``update.*`` entity domain,
  which core, add-ons, HACS repositories and devices with firmware updates all
  publish — the same data Settings > System > Updates shows.
- :func:`get_hacs_info` queries HACS's own WebSocket API, so it only works when
  HACS is installed.
"""

import logging
from typing import Any

from app.api.entities import get_entities
from app.core.decorators import handle_api_errors
from app.core.ws import HassWebSocketError, call_ws

logger = logging.getLogger(__name__)

# HACS names the Lovelace category "plugin" internally; the rest match.
HACS_CATEGORY_MAP = {
    "lovelace": "plugin",
    "integration": "integration",
    "theme": "theme",
    "appdaemon": "appdaemon",
    "python_script": "python_script",
    "template": "template",
}
HACS_CATEGORY_DISPLAY = {value: key for key, value in HACS_CATEGORY_MAP.items()}
HACS_CATEGORY_DISPLAY["plugin"] = "lovelace"


@handle_api_errors
async def get_available_updates() -> dict[str, Any]:
    """
    List Home Assistant update entities that have an update pending.

    Args:
        None

    Returns:
        Dictionary with count and updates. Only entities whose state is "on"
        are included; each carries entity_id, title, installed_version,
        latest_version, release_url and in_progress. An empty list means
        everything is current.

    Examples:
        await get_available_updates()
    """
    entities = await get_entities(domain="update", limit=500, lean=False)

    if isinstance(entities, dict) and "error" in entities:
        return {"error": entities["error"], "count": 0, "updates": []}

    updates = []
    for entity in entities:
        if entity.get("state") != "on":
            continue
        attributes = entity.get("attributes", {})
        updates.append(
            {
                "entity_id": entity.get("entity_id"),
                "title": attributes.get("title") or attributes.get("friendly_name"),
                "installed_version": attributes.get("installed_version"),
                "latest_version": attributes.get("latest_version"),
                "release_url": attributes.get("release_url"),
                "in_progress": attributes.get("in_progress", False),
            }
        )

    return {"count": len(updates), "updates": updates}


@handle_api_errors
async def get_hacs_info(
    query: str | None = None,
    category: str | None = None,
    installed_only: bool = True,
    limit: int = 20,
) -> dict[str, Any]:
    """
    List HACS (Home Assistant Community Store) repositories.

    Backed by HACS's own ``hacs/repositories/list`` WebSocket command, so an
    error is returned when HACS is not installed.

    Args:
        query: Case-insensitive substring filter over name, full_name and
               description. Omit to return everything matching the other filters
        category: One of "integration", "lovelace", "theme", "appdaemon",
                  "python_script" or "template"
        installed_only: Only return installed repositories (default: True).
                        Set False to search the whole store
        limit: Maximum repositories to return (default: 20)

    Returns:
        Dictionary with count, total_matches and repositories. Each repository
        carries name, full_name, description, category, installed,
        installed_version and available_version, plus pending_update for
        installed repositories.

    Examples:
        await get_hacs_info()
        await get_hacs_info(query="mushroom", installed_only=False)
        await get_hacs_info(category="lovelace")
    """
    try:
        await call_ws("hacs/info")
    except HassWebSocketError as e:
        return {
            "error": f"HACS is not installed or not responding: {e}",
            "count": 0,
            "repositories": [],
        }

    kwargs: dict[str, Any] = {}
    if category:
        kwargs["categories"] = [HACS_CATEGORY_MAP.get(category, category)]

    all_repositories = await call_ws("hacs/repositories/list", **kwargs) or []

    query_lower = (query or "").lower().strip()
    matches = []
    for repo in all_repositories:
        if installed_only and not repo.get("installed", False):
            continue
        if query_lower:
            haystack = " ".join(
                [
                    repo.get("name") or "",
                    repo.get("full_name") or "",
                    repo.get("description") or "",
                ]
            ).lower()
            if query_lower not in haystack:
                continue

        entry: dict[str, Any] = {
            "name": repo.get("name"),
            "full_name": repo.get("full_name"),
            "description": repo.get("description"),
            "category": HACS_CATEGORY_DISPLAY.get(repo.get("category", ""), repo.get("category")),
            "installed": repo.get("installed", False),
            "installed_version": (repo.get("installed_version") if repo.get("installed") else None),
            "available_version": repo.get("available_version"),
        }
        if repo.get("installed"):
            entry["pending_update"] = repo.get("pending_upgrade", False)
        matches.append(entry)

    matches.sort(key=lambda repo: (repo.get("name") or "").lower())
    total_matches = len(matches)

    return {
        "count": min(total_matches, limit),
        "total_matches": total_matches,
        "repositories": matches[:limit],
    }
