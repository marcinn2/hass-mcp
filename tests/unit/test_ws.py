"""Tests for the Home Assistant WebSocket API client.

Ported alongside the feature from the mstump/hass-mcp fork.
"""

import json
import pathlib
import ssl
from unittest.mock import patch

import pytest

from app.core.ws import (
    HassWebSocketError,
    build_ssl_context,
    build_ws_url,
    call_ws,
)


class TestBuildWsUrl:
    @pytest.mark.parametrize(
        ("base", "expected"),
        [
            ("http://ha.local:8123", "ws://ha.local:8123/api/websocket"),
            ("https://ha.example.com", "wss://ha.example.com/api/websocket"),
            ("http://ha.local:8123/", "ws://ha.local:8123/api/websocket"),
            ("https://ha.example.com:443/", "wss://ha.example.com:443/api/websocket"),
        ],
    )
    def test_derives_ws_url(self, base, expected):
        assert build_ws_url(base) == expected

    def test_uses_configured_url_by_default(self):
        with patch("app.core.ws.HA_URL", "http://configured:8123"):
            assert build_ws_url() == "ws://configured:8123/api/websocket"

    @pytest.mark.parametrize("base", ["ftp://ha.local", "ha.local:8123", ""])
    def test_rejects_non_http_urls(self, base):
        with pytest.raises(ValueError, match="must start with"):
            build_ws_url(base)


class TestBuildSslContext:
    def test_verifies_by_default(self):
        with patch("app.core.ws.get_ssl_verify_value", return_value=True):
            context = build_ssl_context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True

    def test_verification_can_be_disabled(self):
        with patch("app.core.ws.get_ssl_verify_value", return_value=False):
            context = build_ssl_context()
        assert context.verify_mode == ssl.CERT_NONE
        assert context.check_hostname is False

    def test_custom_ca_bundle(self, tmp_path):
        """A path is loaded as a CA bundle, so it must be a real PEM."""
        default_ca = ssl.get_default_verify_paths().cafile
        if not default_ca:
            pytest.skip("no system CA bundle available to copy")

        ca = tmp_path / "ca.pem"
        ca.write_bytes(pathlib.Path(default_ca).read_bytes())

        with patch("app.core.ws.get_ssl_verify_value", return_value=str(ca)):
            context = build_ssl_context()
        assert context.verify_mode == ssl.CERT_REQUIRED


class FakeWebSocket:
    """Minimal stand-in for a websockets client connection."""

    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        if not self._incoming:
            raise AssertionError("FakeWebSocket ran out of messages")
        return json.dumps(self._incoming.pop(0))

    async def send(self, payload):
        self.sent.append(json.loads(payload))


def fake_connect(incoming):
    """Return a websockets.connect replacement yielding a FakeWebSocket."""
    holder = {}

    def _connect(url, ssl=None, **kwargs):
        holder["url"] = url
        holder["ssl"] = ssl
        holder["kwargs"] = kwargs
        holder["ws"] = FakeWebSocket(incoming)
        return holder["ws"]

    return _connect, holder


@pytest.fixture
def token():
    with patch("app.core.ws.resolve_ha_token", return_value="test-token"):
        yield "test-token"


class TestCallWs:
    async def test_happy_path(self, token):
        connect, holder = fake_connect(
            [
                {"type": "auth_required", "ha_version": "2026.1.0"},
                {"type": "auth_ok"},
                {"id": 1, "type": "result", "success": True, "result": {"sensor.power": [1, 2]}},
            ]
        )
        with patch("app.core.ws.HA_URL", "http://ha.local:8123"):
            with patch("websockets.connect", connect):
                result = await call_ws(
                    "recorder/statistics_during_period", statistic_ids=["sensor.power"]
                )

        assert result == {"sensor.power": [1, 2]}
        assert holder["url"] == "ws://ha.local:8123/api/websocket"
        # ws:// must not carry an SSL context
        assert holder["ssl"] is None

        auth_msg, request_msg = holder["ws"].sent
        assert auth_msg == {"type": "auth", "access_token": "test-token"}
        assert request_msg["type"] == "recorder/statistics_during_period"
        assert request_msg["statistic_ids"] == ["sensor.power"]
        assert request_msg["id"] == 1

    async def test_wss_gets_ssl_context(self, token):
        connect, holder = fake_connect(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {"id": 1, "success": True, "result": []},
            ]
        )
        with patch("app.core.ws.HA_URL", "https://ha.example.com"):
            with patch("websockets.connect", connect):
                await call_ws("ping")
        assert isinstance(holder["ssl"], ssl.SSLContext)

    async def test_auth_invalid_raises(self, token):
        connect, _ = fake_connect(
            [
                {"type": "auth_required"},
                {"type": "auth_invalid", "message": "Invalid access token"},
            ]
        )
        with patch("app.core.ws.HA_URL", "http://ha.local:8123"):
            with patch("websockets.connect", connect):
                with pytest.raises(HassWebSocketError, match="Invalid access token"):
                    await call_ws("ping")

    async def test_unexpected_greeting_raises(self, token):
        connect, _ = fake_connect([{"type": "result"}])
        with patch("app.core.ws.HA_URL", "http://ha.local:8123"):
            with patch("websockets.connect", connect):
                with pytest.raises(HassWebSocketError, match="auth_required"):
                    await call_ws("ping")

    async def test_error_response_raises(self, token):
        connect, _ = fake_connect(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {
                    "id": 1,
                    "success": False,
                    "error": {"code": "invalid_format", "message": "bad period"},
                },
            ]
        )
        with patch("app.core.ws.HA_URL", "http://ha.local:8123"):
            with patch("websockets.connect", connect):
                with pytest.raises(HassWebSocketError, match="invalid_format.*bad period"):
                    await call_ws("recorder/statistics_during_period")

    async def test_skips_messages_for_other_ids(self, token):
        connect, _ = fake_connect(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {"id": 99, "success": True, "result": "not mine"},
                {"id": 1, "success": True, "result": "mine"},
            ]
        )
        with patch("app.core.ws.HA_URL", "http://ha.local:8123"):
            with patch("websockets.connect", connect):
                assert await call_ws("ping") == "mine"

    async def test_missing_token_raises_before_connecting(self):
        with patch("app.core.ws.resolve_ha_token", return_value=""):
            with pytest.raises(HassWebSocketError, match="No Home Assistant token"):
                await call_ws("ping")


class TestMessageSizeLimit:
    """Home Assistant messages routinely exceed the library's 1 MiB default.

    Long-term statistics over months, a detailed automation trace, or the full
    HACS repository list all arrive as one large message; with the default cap
    the connection closes with code 1009 instead. Fix from stosgale/hass-mcp.
    """

    async def test_max_size_is_unset(self, token):
        connect, holder = fake_connect(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {"id": 1, "success": True, "result": {}},
            ]
        )
        with patch("app.core.ws.HA_URL", "http://ha.local:8123"):
            with patch("websockets.connect", connect):
                await call_ws("recorder/statistics_during_period")

        assert holder["kwargs"]["max_size"] is None

    def test_library_default_would_be_too_small(self):
        """Guard the premise: if upstream ever removes the cap, this can go."""
        import inspect

        from websockets.asyncio import client

        default = inspect.signature(client.connect).parameters["max_size"].default
        assert default is not None
        assert default <= 1024 * 1024
