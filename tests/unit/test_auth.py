"""Tests for MCP bearer authentication and Home Assistant token selection.

Contract under test:

- On the HTTP transports a bearer token is always required.
- A token matching MCP_AUTH_TOKENS authenticates the caller, and Home Assistant
  calls use the configured HA token.
- A token matching none of them is treated as that request's Home Assistant
  token when MCP_ALLOW_HA_TOKENS is enabled, and rejected otherwise.
"""

from unittest.mock import patch

import pytest

from app import config
from app.auth import (
    AuthOutcome,
    BearerAuthMiddleware,
    BearerTokenMiddleware,
    auth_is_configured,
    cache_scope,
    classify_token,
    extract_bearer_token,
    is_configured_mcp_token,
    reset_request_token,
    resolve_ha_token,
    set_request_token,
)
from app.config import get_ha_headers

MCP_TOKEN = "mcp-client-secret"
HA_TOKEN = "configured-ha-token"


@pytest.fixture(autouse=True)
def _clean_context():
    """Ensure no per-request token leaks between tests."""
    reset = set_request_token(None)
    yield
    reset_request_token(reset)


@pytest.fixture
def strict():
    """MCP tokens configured; Home Assistant tokens not accepted."""
    with patch.object(config, "MCP_AUTH_TOKENS", [MCP_TOKEN]):
        with patch.object(config, "MCP_ALLOW_HA_TOKENS", False):
            with patch.object(config, "HA_TOKEN", HA_TOKEN):
                yield


@pytest.fixture
def passthrough():
    """MCP tokens configured; unknown tokens forwarded to Home Assistant."""
    with patch.object(config, "MCP_AUTH_TOKENS", [MCP_TOKEN]):
        with patch.object(config, "MCP_ALLOW_HA_TOKENS", True):
            with patch.object(config, "HA_TOKEN", HA_TOKEN):
                yield


async def call_middleware(headers):
    """Drive the middleware; return (status, body, ha_token_seen_by_the_app)."""
    seen = {}
    messages = []

    async def inner(scope, receive, send):
        seen["ha_token"] = resolve_ha_token()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        messages.append(message)

    await BearerAuthMiddleware(inner)({"type": "http", "headers": headers}, no_receive, send)
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return messages[0]["status"], body.decode(), seen.get("ha_token")


def bearer(token):
    return [(b"authorization", f"Bearer {token}".encode())]


async def no_receive():
    """ASGI receive stub; these requests have no body to read."""
    return {"type": "http.request", "body": b"", "more_body": False}


class TestExtractBearerToken:
    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            ([(b"authorization", b"Bearer abc")], "abc"),
            ([(b"authorization", b"bearer abc")], "abc"),
            ([(b"Authorization", b"Bearer abc")], "abc"),
            ([(b"authorization", b"Bearer   abc  ")], "abc"),
            ([(b"authorization", b"Basic abc")], None),
            ([(b"authorization", b"Bearer")], None),
            ([(b"authorization", b"Bearer    ")], None),
            ([(b"accept", b"application/json")], None),
            ([], None),
            (None, None),
        ],
    )
    def test_extraction(self, headers, expected):
        assert extract_bearer_token(headers) == expected


class TestClassifyToken:
    def test_missing(self, strict):
        assert classify_token(None) is AuthOutcome.MISSING
        assert classify_token("") is AuthOutcome.MISSING

    def test_configured_mcp_token(self, strict):
        assert classify_token(MCP_TOKEN) is AuthOutcome.MCP_TOKEN

    def test_unknown_token_is_rejected_by_default(self, strict):
        assert classify_token("something-else") is AuthOutcome.REJECTED

    def test_unknown_token_becomes_ha_token_when_allowed(self, passthrough):
        assert classify_token("something-else") is AuthOutcome.HA_TOKEN

    def test_mcp_token_wins_over_passthrough(self, passthrough):
        """A configured MCP token must not be forwarded to Home Assistant."""
        assert classify_token(MCP_TOKEN) is AuthOutcome.MCP_TOKEN

    def test_multiple_configured_tokens(self):
        with patch.object(config, "MCP_AUTH_TOKENS", ["one", "two", "three"]):
            with patch.object(config, "MCP_ALLOW_HA_TOKENS", False):
                for token in ("one", "two", "three"):
                    assert classify_token(token) is AuthOutcome.MCP_TOKEN
                assert classify_token("four") is AuthOutcome.REJECTED

    def test_matching_is_exact(self, strict):
        for near_miss in (MCP_TOKEN + "x", MCP_TOKEN[:-1], MCP_TOKEN.upper(), " " + MCP_TOKEN):
            assert classify_token(near_miss) is AuthOutcome.REJECTED


class TestIsConfiguredMcpToken:
    def test_no_tokens_configured(self):
        with patch.object(config, "MCP_AUTH_TOKENS", []):
            assert is_configured_mcp_token("anything") is False

    def test_match(self, strict):
        assert is_configured_mcp_token(MCP_TOKEN) is True


class TestAuthIsConfigured:
    def test_tokens_alone_are_enough(self):
        with patch.object(config, "MCP_AUTH_TOKENS", ["t"]):
            with patch.object(config, "MCP_ALLOW_HA_TOKENS", False):
                assert auth_is_configured() is True

    def test_passthrough_alone_is_enough(self):
        with patch.object(config, "MCP_AUTH_TOKENS", []):
            with patch.object(config, "MCP_ALLOW_HA_TOKENS", True):
                assert auth_is_configured() is True

    def test_neither_is_unconfigured(self):
        with patch.object(config, "MCP_AUTH_TOKENS", []):
            with patch.object(config, "MCP_ALLOW_HA_TOKENS", False):
                assert auth_is_configured() is False


class TestBearerRequired:
    """A bearer token is always required on the HTTP transports."""

    async def test_missing_header_is_401(self, strict):
        status, body, _ = await call_middleware([])
        assert status == 401
        assert "missing_token" in body

    async def test_wrong_scheme_is_401(self, strict):
        status, body, _ = await call_middleware([(b"authorization", b"Basic dXNlcjpwdw==")])
        assert status == 401
        assert "missing_token" in body

    async def test_empty_bearer_is_401(self, strict):
        status, body, _ = await call_middleware([(b"authorization", b"Bearer   ")])
        assert status == 401
        assert "missing_token" in body

    async def test_401_carries_www_authenticate(self, strict):
        messages = []

        async def send(message):
            messages.append(message)

        async def inner(scope, receive, send):  # pragma: no cover - must not run
            raise AssertionError("inner app must not be reached")

        await BearerAuthMiddleware(inner)({"type": "http", "headers": []}, no_receive, send)
        headers = dict(messages[0]["headers"])
        assert b"www-authenticate" in headers
        assert headers[b"www-authenticate"].startswith(b"Bearer ")

    async def test_unauthenticated_request_never_reaches_the_app(self, strict):
        reached = {"yes": False}

        async def inner(scope, receive, send):
            reached["yes"] = True

        async def send(message):
            pass

        await BearerAuthMiddleware(inner)({"type": "http", "headers": []}, no_receive, send)
        assert reached["yes"] is False


class TestConfiguredMcpTokenUsesConfiguredHaToken:
    async def test_accepted_and_uses_configured_ha_token(self, strict):
        status, body, ha_token = await call_middleware(bearer(MCP_TOKEN))
        assert status == 200
        assert body == "ok"
        assert ha_token == HA_TOKEN

    async def test_also_in_passthrough_mode(self, passthrough):
        status, _, ha_token = await call_middleware(bearer(MCP_TOKEN))
        assert status == 200
        assert ha_token == HA_TOKEN


class TestUnknownTokenHandling:
    async def test_rejected_when_ha_tokens_not_allowed(self, strict):
        status, body, _ = await call_middleware(bearer("unknown-token"))
        assert status == 401
        assert "invalid_token" in body

    async def test_forwarded_to_ha_when_allowed(self, passthrough):
        """The presented token replaces the configured one for this request."""
        status, _, ha_token = await call_middleware(bearer("caller-own-ha-token"))
        assert status == 200
        assert ha_token == "caller-own-ha-token"
        assert ha_token != HA_TOKEN

    async def test_forwarded_token_reaches_request_headers(self, passthrough):
        seen = {}

        async def inner(scope, receive, send):
            seen["auth"] = get_ha_headers().get("Authorization")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def send(message):
            pass

        await BearerAuthMiddleware(inner)(
            {"type": "http", "headers": bearer("caller-own-ha-token")}, no_receive, send
        )
        assert seen["auth"] == "Bearer caller-own-ha-token"

    async def test_context_is_reset_after_the_request(self, passthrough):
        status, _, ha_token = await call_middleware(bearer("caller-own-ha-token"))
        assert ha_token == "caller-own-ha-token"
        # Outside the request the configured token applies again
        assert resolve_ha_token() == HA_TOKEN

    async def test_requests_do_not_leak_between_each_other(self, passthrough):
        _, _, first = await call_middleware(bearer("token-one"))
        _, _, second = await call_middleware(bearer(MCP_TOKEN))
        _, _, third = await call_middleware(bearer("token-three"))
        assert (first, second, third) == ("token-one", HA_TOKEN, "token-three")


class TestNonHttpScopes:
    async def test_lifespan_passes_through_unauthenticated(self, strict):
        called = {}

        async def inner(scope, receive, send):
            called["type"] = scope["type"]

        async def send(message):  # pragma: no cover - nothing should be sent
            raise AssertionError("no response expected")

        await BearerAuthMiddleware(inner)({"type": "lifespan"}, no_receive, send)
        assert called["type"] == "lifespan"


class TestStdioUnaffected:
    """stdio installs no middleware, so the configured token is used."""

    def test_resolve_falls_back_to_configured_token(self):
        with patch.object(config, "HA_TOKEN", HA_TOKEN):
            assert resolve_ha_token() == HA_TOKEN

    def test_headers_use_configured_token(self):
        with patch.object(config, "HA_TOKEN", HA_TOKEN):
            assert get_ha_headers()["Authorization"] == f"Bearer {HA_TOKEN}"

    def test_no_authorization_header_without_token(self):
        with patch.object(config, "HA_TOKEN", ""):
            assert "Authorization" not in get_ha_headers()


class TestBackwardsCompatibleAlias:
    def test_old_name_still_resolves(self):
        assert BearerTokenMiddleware is BearerAuthMiddleware


class TestCacheScope:
    """Cached responses must not cross callers.

    With MCP_ALLOW_HA_TOKENS each request can carry its own Home Assistant
    token, and Home Assistant answers per that user's permissions. An
    unpartitioned cache would serve one user another user's data — and on a
    cache hit Home Assistant is never consulted, so its permission model is
    bypassed entirely.
    """

    def test_no_scope_without_a_request_token(self):
        """Single-token deployments keep unpartitioned keys, losing no cache."""
        assert cache_scope() is None

    def test_scope_present_with_a_request_token(self):
        reset = set_request_token("user-a-token")
        try:
            assert cache_scope() is not None
        finally:
            reset_request_token(reset)

    def test_scope_differs_per_token(self):
        reset = set_request_token("user-a-token")
        a = cache_scope()
        reset_request_token(reset)

        reset = set_request_token("user-b-token")
        b = cache_scope()
        reset_request_token(reset)

        assert a != b

    def test_scope_is_stable_for_a_token(self):
        reset = set_request_token("user-a-token")
        first = cache_scope()
        reset_request_token(reset)

        reset = set_request_token("user-a-token")
        second = cache_scope()
        reset_request_token(reset)

        assert first == second

    def test_scope_never_contains_the_token(self):
        """The scope lands in cache keys and log lines."""
        token = "super-secret-ha-token"
        reset = set_request_token(token)
        try:
            scope = cache_scope()
        finally:
            reset_request_token(reset)

        assert token not in scope
        assert len(scope) == 16

    def test_cache_keys_are_partitioned_by_caller(self):
        from app.core.cache.decorator import _build_cache_key

        async def get_entities(domain: str | None = None) -> list:  # noqa: ARG001
            return []

        def key():
            return _build_cache_key(
                func=get_entities,
                args=(),
                kwargs={},
                key_prefix="entities",
                include_params=None,
                exclude_params=None,
            )

        unscoped = key()

        reset = set_request_token("user-a-token")
        a = key()
        reset_request_token(reset)

        reset = set_request_token("user-b-token")
        b = key()
        reset_request_token(reset)

        assert a != b, "two callers must not share a cache entry"
        assert unscoped != a
        assert "__scope" in a
