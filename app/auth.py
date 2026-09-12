"""Bearer authentication for the MCP server, and Home Assistant token plumbing.

The per-request token mechanism (a ``ContextVar`` populated by ASGI middleware)
is ported from the mstump/hass-mcp fork; the MCP bearer authentication built on
top of it is specific to this project.

Two related concerns live here:

1. **Authenticating the MCP client.** Over the HTTP transports every request
   must carry ``Authorization: Bearer <token>``. A token listed in
   ``MCP_AUTH_TOKENS`` authenticates the caller, and Home Assistant calls then
   use the configured HA token.

2. **Choosing the Home Assistant token for the request.** When
   ``MCP_ALLOW_HA_TOKENS`` is enabled, a bearer token that matches none of the
   configured MCP tokens is instead treated as a Home Assistant token and used
   for that request only — so each client can bring its own HA credential
   rather than sharing the server's. Home Assistant itself is then the
   authority on whether the token is valid.

   With that option disabled, an unrecognised token is rejected with 401.

The per-request HA token travels in a ``ContextVar``, which propagates across
asyncio task boundaries within a request, so tool handlers need no changes.

stdio mode has no request layer: the middleware is never installed, nothing is
enforced, and the configured ``HA_TOKEN`` is used as before.

This module deliberately imports nothing from ``app`` at module scope so that
``app.config`` can use it without an import cycle.
"""

import hashlib
import json
import logging
import secrets
from contextvars import ContextVar
from enum import Enum

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_bearer_token: ContextVar[str | None] = ContextVar("ha_bearer_token", default=None)


class AuthOutcome(Enum):
    """How a presented bearer token was classified."""

    MISSING = "missing"
    """No usable ``Authorization: Bearer`` header was present."""

    MCP_TOKEN = "mcp_token"  # nosec B105 - an enum member value, not a credential
    """Matched a configured MCP token; use the configured HA token."""

    HA_TOKEN = "ha_token"  # nosec B105 - an enum member value, not a credential
    """Unrecognised, but HA tokens are allowed; forward it to Home Assistant."""

    REJECTED = "rejected"
    """Unrecognised and HA tokens are not allowed."""


def resolve_ha_token() -> str:
    """
    Return the Home Assistant token to use for the current call.

    A per-request token set by :class:`BearerAuthMiddleware` wins; otherwise the
    configured ``HA_TOKEN`` is used. The fallback is read from ``app.config`` at
    call time rather than import time, so tests that patch
    ``app.config.HA_TOKEN`` see the patched value.

    Returns:
        The bearer token, or an empty string when none is configured
    """
    token = _bearer_token.get()
    if token:
        return token

    from app import config  # noqa: PLC0415 - lazy: avoids app.config <-> app.auth cycle

    return config.HA_TOKEN


def cache_scope() -> str | None:
    """
    Return a cache-partition key for the caller, or None when partitioning is
    unnecessary.

    Cached responses must not cross callers. When a request supplies its own
    Home Assistant token (``MCP_ALLOW_HA_TOKENS``), Home Assistant applies that
    user's permissions — so two callers can legitimately get different answers
    to an identical query, and a shared cache entry would serve one user the
    other's data.

    Returns None when no per-request token is set, so single-token deployments
    keep using unpartitioned keys and lose nothing on upgrade.

    Returns:
        A short, stable digest of the per-request token, or None

    Examples:
        >>> cache_scope() is None  # stdio, or no bearer token forwarded
        True
    """
    token = _bearer_token.get()
    if not token:
        return None
    # Truncated digest: enough to partition, and never the token itself, which
    # would otherwise end up in cache keys and log lines.
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def set_request_token(token: str | None):
    """
    Set the per-request Home Assistant token directly.

    Intended for tests and for transports that are not ASGI-based.

    Args:
        token: The token to use, or None to clear it

    Returns:
        A reset token to pass to :func:`reset_request_token`
    """
    return _bearer_token.set(token)


def reset_request_token(reset_token) -> None:
    """
    Restore the per-request token to its previous value.

    Args:
        reset_token: The value returned by :func:`set_request_token`
    """
    _bearer_token.reset(reset_token)


def extract_bearer_token(headers) -> str | None:
    """
    Pull the bearer token out of raw ASGI headers.

    Args:
        headers: The ASGI scope's list of (name, value) byte pairs

    Returns:
        The token, or None when no usable bearer credential is present

    Examples:
        >>> extract_bearer_token([(b"authorization", b"Bearer abc")])
        'abc'
        >>> extract_bearer_token([(b"authorization", b"Basic abc")]) is None
        True
    """
    for name, value in headers or []:
        if name.lower() == b"authorization":
            decoded = value.decode("latin-1")
            if decoded.lower().startswith("bearer "):
                return decoded[7:].strip() or None
            return None
    return None


def is_configured_mcp_token(token: str) -> bool:
    """
    Check a token against the configured MCP tokens in constant time.

    Args:
        token: The presented bearer token

    Returns:
        True if it matches any configured MCP token
    """
    from app import config  # noqa: PLC0415 - lazy: avoids app.config <-> app.auth cycle

    # compare_digest on every candidate: no early exit, so a match position
    # cannot be inferred from timing.
    matched = False
    for candidate in config.MCP_AUTH_TOKENS:
        if secrets.compare_digest(token, candidate):
            matched = True
    return matched


def classify_token(token: str | None) -> AuthOutcome:
    """
    Decide what a presented bearer token means.

    Args:
        token: The presented token, or None when absent

    Returns:
        The :class:`AuthOutcome` for this token

    Examples:
        classify_token(None) -> AuthOutcome.MISSING
        classify_token("<configured>") -> AuthOutcome.MCP_TOKEN
        classify_token("<unknown>") -> AuthOutcome.HA_TOKEN or REJECTED
    """
    if not token:
        return AuthOutcome.MISSING

    if is_configured_mcp_token(token):
        return AuthOutcome.MCP_TOKEN

    from app import config  # noqa: PLC0415 - lazy: avoids app.config <-> app.auth cycle

    if config.MCP_ALLOW_HA_TOKENS:
        return AuthOutcome.HA_TOKEN

    return AuthOutcome.REJECTED


def auth_is_configured() -> bool:
    """
    Report whether the HTTP transports have a usable authentication setup.

    Either configured MCP tokens or the pass-through HA-token mode is enough;
    with neither, every request would be rejected.

    Returns:
        True when at least one authentication path is available
    """
    from app import config  # noqa: PLC0415 - lazy: avoids app.config <-> app.auth cycle

    return bool(config.MCP_AUTH_TOKENS) or config.MCP_ALLOW_HA_TOKENS


class BearerAuthMiddleware:
    """
    ASGI middleware enforcing bearer authentication on the HTTP transports.

    Every HTTP request must present ``Authorization: Bearer <token>``:

    - a token matching ``MCP_AUTH_TOKENS`` is accepted, and the configured Home
      Assistant token is used for that request;
    - otherwise, if ``MCP_ALLOW_HA_TOKENS`` is enabled, the token is treated as
      a Home Assistant token and used for that request only;
    - otherwise the request is refused with 401.

    Non-HTTP scopes (lifespan, websocket) pass through untouched.

    Examples:
        app = mcp.streamable_http_app()
        app.add_middleware(BearerAuthMiddleware)
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = extract_bearer_token(scope.get("headers", []))
        outcome = classify_token(token)

        if outcome is AuthOutcome.MISSING:
            await self._unauthorized(
                send,
                "missing_token",
                "An Authorization: Bearer <token> header is required.",
            )
            return

        if outcome is AuthOutcome.REJECTED:
            logger.warning("Rejected MCP request: bearer token did not match any configured token")
            await self._unauthorized(
                send,
                "invalid_token",
                "The bearer token is not recognised.",
            )
            return

        # MCP_TOKEN uses the configured HA token, so the per-request slot is
        # explicitly cleared; HA_TOKEN forwards the presented credential.
        request_token = token if outcome is AuthOutcome.HA_TOKEN else None
        if outcome is AuthOutcome.HA_TOKEN:
            logger.debug("Forwarding the presented bearer token to Home Assistant")

        reset_token = _bearer_token.set(request_token)
        try:
            await self.app(scope, receive, send)
        finally:
            _bearer_token.reset(reset_token)

    @staticmethod
    async def _unauthorized(send: Send, error: str, description: str) -> None:
        """Send an RFC 6750 style 401 response."""
        body = json.dumps({"error": error, "error_description": description}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (
                        b"www-authenticate",
                        f'Bearer error="{error}", error_description="{description}"'.encode(),
                    ),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


# Backwards-compatible alias: the middleware used to only capture a token.
BearerTokenMiddleware = BearerAuthMiddleware
