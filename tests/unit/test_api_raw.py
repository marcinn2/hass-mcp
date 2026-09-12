"""Tests for the generic Home Assistant REST passthrough.

Ported alongside the feature from the FriendlyVoid/hass-mcp fork.

The passthrough deliberately accepts arbitrary paths, so the guardrails that do
exist — method validation and confinement to "/api/" — are what these tests pin
down.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.raw import ALLOWED_METHODS, call_ha_api
from app.tools.raw import call_api


@pytest.fixture(autouse=True)
def _token():
    """The passthrough refuses to run without a resolvable token."""
    with patch("app.api.raw.resolve_ha_token", return_value="test-token"):
        yield


@pytest.fixture
def captured():
    return {}


@pytest.fixture
def fake_client(captured):
    client = MagicMock()

    async def _request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["params"] = kwargs.get("params")
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.content = b'{"ok": true}'
        response.status_code = 200
        response.json = MagicMock(return_value={"ok": True})
        return response

    client.request = _request
    return client


def patched_client(fake_client):
    return patch("app.api.raw.get_client", new=AsyncMock(return_value=fake_client))


class TestMethodHandling:
    @pytest.mark.parametrize("method", ALLOWED_METHODS)
    async def test_allowed_methods(self, method, captured, fake_client):
        with patched_client(fake_client):
            await call_ha_api(method, "/api/services")
        assert captured["method"] == method

    @pytest.mark.parametrize("method", ["get", "PoSt", " put "])
    async def test_method_is_normalised(self, method, captured, fake_client):
        with patched_client(fake_client):
            await call_ha_api(method, "/api/services")
        assert captured["method"] == method.strip().upper()

    @pytest.mark.parametrize("method", ["TRACE", "CONNECT", "OPTIONS", "HEAD", ""])
    async def test_disallowed_methods_are_refused(self, method, fake_client):
        with patched_client(fake_client):
            result = await call_ha_api(method, "/api/services")
        assert "method must be one of" in result["error"]


class TestPathHandling:
    async def test_leading_slash_is_optional(self, captured, fake_client):
        with patched_client(fake_client):
            await call_ha_api("GET", "api/services")
        assert captured["url"].endswith("/api/services")

    async def test_path_outside_api_is_refused(self, fake_client):
        with patched_client(fake_client):
            result = await call_ha_api("GET", "/auth/token")
        assert "must start with '/api/'" in result["error"]

    @pytest.mark.parametrize(
        "path",
        ["http://evil.example/x", "https://evil.example/x", "//evil.example/x"],
    )
    async def test_absolute_and_protocol_relative_urls_are_refused(self, path, fake_client):
        with patched_client(fake_client):
            result = await call_ha_api("GET", path)
        assert "not a URL" in result["error"]

    async def test_request_stays_on_the_configured_host(self, captured, fake_client):
        with patch("app.api.raw.HA_URL", "http://ha.local:8123"):
            with patched_client(fake_client):
                await call_ha_api("GET", "/api/config")
        assert captured["url"] == "http://ha.local:8123/api/config"


class TestPayloadAndResponse:
    async def test_body_and_params_are_forwarded(self, captured, fake_client):
        with patched_client(fake_client):
            await call_ha_api(
                "POST",
                "/api/template",
                body={"template": "{{ 1 + 1 }}"},
                params={"foo": "bar"},
            )
        assert captured["json"] == {"template": "{{ 1 + 1 }}"}
        assert captured["params"] == {"foo": "bar"}

    async def test_json_response_is_returned(self, fake_client):
        with patched_client(fake_client):
            assert await call_ha_api("GET", "/api/services") == {"ok": True}

    async def test_empty_response_reports_status(self):
        client = MagicMock()

        async def _request(method, url, **kwargs):
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.content = b""
            response.status_code = 204
            return response

        client.request = _request
        with patch("app.api.raw.get_client", new=AsyncMock(return_value=client)):
            result = await call_ha_api("DELETE", "/api/config/thing/1")
        assert result == {"_status": 204, "_text": ""}

    async def test_non_json_response_reports_text(self):
        client = MagicMock()

        async def _request(method, url, **kwargs):
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.content = b"plain text"
            response.status_code = 200
            response.json = MagicMock(side_effect=ValueError("not json"))
            response.text = "plain text"
            return response

        client.request = _request
        with patch("app.api.raw.get_client", new=AsyncMock(return_value=client)):
            result = await call_ha_api("GET", "/api/error_log")
        assert result == {"_status": 200, "_text": "plain text"}


class TestCallApiTool:
    async def test_tool_delegates_to_the_api(self):
        api = AsyncMock(return_value={"ok": True})
        with patch("app.tools.raw.call_ha_api", api):
            result = await call_api("GET", "/api/services", params={"a": "b"})

        assert api.await_args.args == ("GET", "/api/services", None, {"a": "b"})
        assert result == {"ok": True}


class TestNotExposedByDefault:
    def test_call_api_is_off_by_default(self):
        from app.tools.registry import DEFAULT_ENABLED, TOOLS_BY_NAME

        assert TOOLS_BY_NAME["call_api"].default is False
        assert "call_api" not in DEFAULT_ENABLED

    def test_it_can_be_enabled(self):
        from app.tools.registry import resolve_enabled

        specs, unknown = resolve_enabled(["call_api"])
        assert [s.name for s in specs] == ["call_api"]
        assert unknown == []


class TestErrorPaths:
    """Transport failures must come back as {"error": ...}, not raise."""

    async def _failing(self, exc):

        client = MagicMock()

        async def _request(method, url, **kwargs):
            raise exc

        client.request = _request
        with patch("app.api.raw.get_client", new=AsyncMock(return_value=client)):
            return await call_ha_api("GET", "/api/config")

    async def test_connection_error(self):
        import httpx

        result = await self._failing(httpx.ConnectError("boom"))
        assert "Connection error" in result["error"]

    async def test_timeout(self):
        import httpx

        result = await self._failing(httpx.TimeoutException("slow"))
        assert "Timeout error" in result["error"]

    async def test_http_status_error(self):
        import httpx

        response = MagicMock()
        response.status_code = 404
        response.reason_phrase = "Not Found"
        result = await self._failing(
            httpx.HTTPStatusError("nope", request=MagicMock(), response=response)
        )
        assert "HTTP error: 404 Not Found" in result["error"]

    async def test_unexpected_error(self):
        result = await self._failing(RuntimeError("surprise"))
        assert "Unexpected error: surprise" in result["error"]

    async def test_missing_token(self):
        with patch("app.api.raw.resolve_ha_token", return_value=""):
            result = await call_ha_api("GET", "/api/config")
        assert "No Home Assistant token" in result["error"]
