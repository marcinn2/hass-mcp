"""Home Assistant WebSocket API client.

Ported from the mstump/hass-mcp fork, with TLS handling adapted to this
project's explicit ``HA_SSL_VERIFY`` setting instead of the OS trust store.

Some Home Assistant data is only available over the WebSocket API — notably
``recorder/statistics_during_period``, which serves the long-term statistics
that survive the recorder's short-term purge window. The REST API has no
equivalent.

Each call opens a fresh connection, authenticates with the resolved token,
sends one request and returns the result. Connections are not pooled: HA's
WebSocket auth is a single round-trip, and statistics calls are not on a hot
path, so pooling would trade complexity for a marginal win.
"""

import json
import logging
import ssl
from typing import Any

from app.auth import resolve_ha_token
from app.config import HA_URL, get_ssl_verify_value

logger = logging.getLogger(__name__)

# Guard against a malformed or hostile response stream growing without bound.
MAX_AUTH_MESSAGES = 10


class HassWebSocketError(Exception):
    """Raised when the Home Assistant WebSocket API returns an error."""


def build_ws_url(ha_url: str | None = None) -> str:
    """
    Derive the WebSocket endpoint from the configured Home Assistant URL.

    Args:
        ha_url: Override for the base URL (defaults to the configured HA_URL)

    Returns:
        The ``ws://`` or ``wss://`` URL of the HA WebSocket API

    Raises:
        ValueError: If the base URL is not http(s)

    Examples:
        >>> build_ws_url("http://ha.local:8123")
        'ws://ha.local:8123/api/websocket'
        >>> build_ws_url("https://ha.example.com")
        'wss://ha.example.com/api/websocket'
    """
    base = (ha_url if ha_url is not None else HA_URL).rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :] + "/api/websocket"
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :] + "/api/websocket"
    raise ValueError(f"HA_URL must start with http:// or https://, got: {base!r}")


def build_ssl_context() -> ssl.SSLContext | None:
    """
    Build an SSL context mirroring the REST client's verification policy.

    Honours ``HA_SSL_VERIFY``: ``true`` verifies against the system CA store,
    ``false`` disables verification, and a path is used as a CA bundle.

    Returns:
        An SSLContext, or None when verification is disabled in a way the
        websockets library expresses as ``ssl=None``
    """
    verify = get_ssl_verify_value()

    if verify is False:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        logger.warning("HA WebSocket TLS verification is disabled (HA_SSL_VERIFY=false)")
        return context

    if isinstance(verify, str):
        return ssl.create_default_context(cafile=verify)

    return ssl.create_default_context()


async def call_ws(message_type: str, **payload: Any) -> Any:
    """
    Send one request over the Home Assistant WebSocket API and return its result.

    Args:
        message_type: The HA message type, e.g. "recorder/statistics_during_period"
        **payload: Additional fields merged into the request body

    Returns:
        The ``result`` field of HA's success response; the shape depends on the
        message type (dict, list, ...)

    Raises:
        HassWebSocketError: If authentication fails or HA reports success=False
        ValueError: If HA_URL is not http(s)

    Examples:
        await call_ws(
            "recorder/statistics_during_period",
            start_time="2026-01-01T00:00:00Z",
            statistic_ids=["sensor.power"],
            period="hour",
        )
    """
    try:
        import websockets  # noqa: PLC0415 - lazy: report a clear error if absent
    except ImportError as e:  # pragma: no cover - websockets is a declared dependency
        raise HassWebSocketError(
            "The websockets package is required for Home Assistant WebSocket calls. "
            "Install it with: uv sync"
        ) from e

    token = resolve_ha_token()
    if not token:
        raise HassWebSocketError(
            "No Home Assistant token available. Set HA_TOKEN, configure "
            "home_assistant.token, or supply an Authorization header."
        )

    url = build_ws_url()
    ssl_context = build_ssl_context() if url.startswith("wss://") else None

    logger.debug(f"Opening HA WebSocket connection for {message_type}")

    # max_size=None lifts the library's 1 MiB per-message cap, which Home
    # Assistant routinely exceeds: long-term statistics over months, a detailed
    # automation trace, or the full HACS repository list all arrive as a single
    # large message and would otherwise close the connection with code 1009.
    # Fix from the stosgale/hass-mcp fork.
    async with websockets.connect(url, ssl=ssl_context, max_size=None) as ws:
        # Home Assistant sends auth_required first, then expects an auth message.
        greeting = json.loads(await ws.recv())
        if greeting.get("type") != "auth_required":
            raise HassWebSocketError(
                f"Expected 'auth_required' from Home Assistant, got {greeting.get('type')!r}"
            )

        await ws.send(json.dumps({"type": "auth", "access_token": token}))

        # HA may interleave informational messages before auth_ok.
        for _ in range(MAX_AUTH_MESSAGES):
            auth_result = json.loads(await ws.recv())
            result_type = auth_result.get("type")
            if result_type == "auth_ok":
                break
            if result_type == "auth_invalid":
                raise HassWebSocketError(
                    f"Home Assistant rejected the token: "
                    f"{auth_result.get('message', 'auth_invalid')}"
                )
        else:
            raise HassWebSocketError(
                f"Home Assistant did not confirm authentication within {MAX_AUTH_MESSAGES} messages"
            )

        request_id = 1
        await ws.send(json.dumps({"id": request_id, "type": message_type, **payload}))

        # Skip anything that is not the reply to our request id.
        while True:
            message = json.loads(await ws.recv())
            if message.get("id") != request_id:
                continue
            if not message.get("success", False):
                error = message.get("error") or {}
                raise HassWebSocketError(
                    f"Home Assistant WebSocket call {message_type!r} failed: "
                    f"{error.get('code', 'unknown')} - {error.get('message', 'no message')}"
                )
            return message.get("result")
