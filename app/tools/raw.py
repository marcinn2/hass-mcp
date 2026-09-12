"""Generic REST passthrough MCP tool.

Ported from the FriendlyVoid/hass-mcp fork.

Not exposed by default: it forwards arbitrary requests using the server's Home
Assistant credentials, so it bypasses the narrower contracts of every other
tool. Enable it deliberately via ``tools.enabled`` when the typed tools do not
cover what you need.
"""

import logging
from typing import Any

from app.api.raw import call_ha_api

logger = logging.getLogger(__name__)


async def call_api(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    """
    Call the Home Assistant REST API directly, for endpoints with no dedicated tool.

    Prefer a dedicated tool wherever one exists — get_entity, entity_action,
    call_service_tool, list_items, get_item, manage_item and the rest have
    clearer schemas and better error messages. Reach for this only when nothing
    else fits.

    Args:
        method: HTTP method - "GET", "POST", "PUT", "PATCH" or "DELETE"
                (case-insensitive)
        path: API path under "/api/", leading slash optional, e.g.
              "/api/template" or "api/config/script/config/1734567890123"
        body: Optional JSON body for POST/PUT/PATCH
        params: Optional query string parameters

    Returns:
        The parsed JSON response, or {"_status": <code>, "_text": <body>} when
        the response is empty or not JSON. On failure, a dictionary with an
        "error" key.

    Examples:
        method="GET", path="/api/services" - List every service the instance exposes
        method="POST", path="/api/template", body={"template": "{{ states('sun.sun') }}"}
            - Render a Jinja template against current state
        method="GET", path="/api/config/script/config/1734567890123"
            - Fetch a script's full config
        method="POST", path="/api/events/my_custom_event", body={"data": "value"}
            - Fire a custom event

    Best Practices:
        - Check whether a dedicated tool covers the endpoint first
        - Results are not cached and no cache is invalidated, so a write here
          may leave other tools serving stale data until their TTL expires
        - Only paths under "/api/" are permitted
    """
    logger.info(f"call_api: {method} {path}")
    return await call_ha_api(method, path, body, params)
