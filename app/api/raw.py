"""Generic Home Assistant REST passthrough.

Ported from the FriendlyVoid/hass-mcp fork.

An escape hatch for endpoints that have no dedicated tool. Everything reachable
through the typed API modules should go through them instead: they have narrower
schemas, clearer errors, and benefit from caching and invalidation. This exists
for the long tail.

Because it forwards arbitrary paths with the server's Home Assistant
credentials, the tool built on it is **not exposed by default** — see
``app.tools.registry``.

Errors are returned as ``{"error": ...}`` rather than going through
``handle_api_errors``: that decorator infers its error shape from the return
annotation, and this function honestly returns ``Any``, which would yield bare
strings.
"""

import logging
from typing import Any

import httpx

from app.auth import resolve_ha_token
from app.config import HA_URL, get_ha_headers
from app.core import get_client

logger = logging.getLogger(__name__)

# Methods the passthrough will issue.
ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# Every Home Assistant REST endpoint lives under this prefix. Requiring it keeps
# the passthrough off the frontend and auth routes, which are not an API and
# have no business being driven by a tool.
REQUIRED_PREFIX = "/api/"


async def call_ha_api(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    """
    Make an arbitrary call against the Home Assistant REST API.

    Args:
        method: HTTP method; one of GET, POST, PUT, PATCH, DELETE (any case)
        path: API path under "/api/", with or without the leading slash
        body: Optional JSON body for POST/PUT/PATCH
        params: Optional query string parameters

    Returns:
        The parsed JSON response, which may be any JSON type. When the body is
        empty or not JSON, {"_status": <code>, "_text": <raw body>}. On failure,
        {"error": <message>}.

    Examples:
        await call_ha_api("GET", "/api/services")
        await call_ha_api("POST", "/api/template", body={"template": "{{ 1 + 1 }}"})
    """
    # resolve_ha_token honours a per-request bearer token, so this must not read
    # the module-level HA_TOKEN or the HTTP pass-through mode would be refused.
    if not resolve_ha_token():
        return {"error": "No Home Assistant token available. Please set HA_TOKEN."}

    normalized_method = method.strip().upper()
    if normalized_method not in ALLOWED_METHODS:
        return {"error": f"method must be one of {list(ALLOWED_METHODS)}, got {method!r}"}

    candidate = path.strip()
    if "://" in candidate or candidate.startswith("//"):
        return {"error": f"path must be a Home Assistant API path, not a URL: {path!r}"}
    if not candidate.startswith("/"):
        candidate = "/" + candidate
    if not candidate.startswith(REQUIRED_PREFIX):
        return {
            "error": (
                f"path must start with {REQUIRED_PREFIX!r}, got {path!r}. "
                "Only the Home Assistant REST API is reachable through this call."
            )
        }

    logger.info(f"Passthrough request: {normalized_method} {candidate}")

    try:
        client = await get_client()
        response = await client.request(
            method=normalized_method,
            url=f"{HA_URL}{candidate}",
            headers=get_ha_headers(),
            json=body if body is not None else None,
            params=params or None,
        )
        response.raise_for_status()
    except httpx.ConnectError:
        return {"error": f"Connection error: Cannot connect to Home Assistant at {HA_URL}"}
    except httpx.TimeoutException:
        return {"error": f"Timeout error: Home Assistant at {HA_URL} did not respond in time"}
    except httpx.HTTPStatusError as e:
        return {
            "error": (
                f"HTTP error: {e.response.status_code} {e.response.reason_phrase} "
                f"for {normalized_method} {candidate}"
            )
        }
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}

    if not response.content:
        return {"_status": response.status_code, "_text": ""}

    try:
        return response.json()
    except ValueError:
        return {"_status": response.status_code, "_text": response.text}
