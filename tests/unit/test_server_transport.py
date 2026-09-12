"""Tests for transport configuration.

Regression coverage for a defect where MCP_HOST and MCP_PORT were read and
logged inside run_server but never applied: FastMCP.run() accepts only
(transport, mount_path), so the bind address has to reach the constructor.
"""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest


def _reload_server(env):
    """Re-import the config chain and app.server with the given environment.

    Settings are resolved at import time, so the configuration modules have to
    be reloaded before app.server picks up new values.
    """
    import app.config
    import app.config_file
    import app.server

    with patch.dict(os.environ, env, clear=False):
        importlib.reload(app.config_file)
        importlib.reload(app.config)
        return importlib.reload(app.server)


@pytest.fixture(autouse=True)
def _restore_server():
    """Leave the config chain reloaded from a clean environment."""
    yield
    for var in (
        "MCP_HOST",
        "MCP_PORT",
        "PORT",
        "MCP_TRANSPORT",
        "HASS_MCP_CONFIG_FILE",
        "MCP_AUTH_TOKENS",
        "MCP_ALLOW_HA_TOKENS",
    ):
        os.environ.pop(var, None)
    _reload_server({})


class TestBindAddress:
    def test_defaults(self):
        server = _reload_server({})
        assert server.mcp.settings.host == "127.0.0.1"
        assert server.mcp.settings.port == 8000

    def test_mcp_host_and_port_are_applied(self):
        server = _reload_server({"MCP_HOST": "0.0.0.0", "MCP_PORT": "9999"})
        assert server.mcp.settings.host == "0.0.0.0"
        assert server.mcp.settings.port == 9999

    def test_port_variable_is_honoured_for_smithery(self):
        os.environ.pop("MCP_PORT", None)
        server = _reload_server({"PORT": "7777"})
        assert server.mcp.settings.port == 7777

    def test_mcp_port_overrides_port(self):
        server = _reload_server({"MCP_PORT": "9001", "PORT": "7777"})
        assert server.mcp.settings.port == 9001

    def test_version_is_reported(self):
        server = _reload_server({})
        assert server.mcp._mcp_server.version == server.__version__


class TestRunServer:
    """HTTP transports are served through uvicorn so middleware can be added.

    uvicorn.run blocks, so it must always be patched here — an unpatched call
    starts a real server and hangs the suite.
    """

    @pytest.mark.parametrize(
        ("transport", "app_factory"),
        [("sse", "sse_app"), ("streamable-http", "streamable_http_app")],
    )
    def test_http_transport_serves_via_uvicorn(self, transport, app_factory):
        # Bearer auth is mandatory on the HTTP transports, so a token is needed
        # for the server to start at all.
        server = _reload_server(
            {"MCP_TRANSPORT": transport, "MCP_PORT": "9123", "MCP_AUTH_TOKENS": "client-token"}
        )
        fake_app = MagicMock()

        with patch("uvicorn.run", new=MagicMock()) as uvicorn_run:
            with patch.object(server.mcp, app_factory, new=MagicMock(return_value=fake_app)):
                with patch.object(server.mcp, "run", new=MagicMock()) as mcp_run:
                    server.run_server()

        # Served by uvicorn, not FastMCP's own runner
        mcp_run.assert_not_called()
        uvicorn_run.assert_called_once()
        assert uvicorn_run.call_args.args[0] is fake_app
        assert uvicorn_run.call_args.kwargs["host"] == "127.0.0.1"
        assert uvicorn_run.call_args.kwargs["port"] == 9123

    @pytest.mark.parametrize(
        ("transport", "app_factory"),
        [("sse", "sse_app"), ("streamable-http", "streamable_http_app")],
    )
    def test_bearer_token_middleware_is_installed(self, transport, app_factory):
        from app.auth import BearerTokenMiddleware

        server = _reload_server({"MCP_TRANSPORT": transport, "MCP_AUTH_TOKENS": "client-token"})
        fake_app = MagicMock()

        with patch("uvicorn.run", new=MagicMock()):
            with patch.object(server.mcp, app_factory, new=MagicMock(return_value=fake_app)):
                server.run_server()

        fake_app.add_middleware.assert_called_once_with(BearerTokenMiddleware)

    def test_stdio_is_the_default(self):
        server = _reload_server({})
        with patch.object(server.mcp, "run", new=MagicMock()) as run:
            server.run_server()
        run.assert_called_once_with()

    def test_stdio_does_not_start_uvicorn(self):
        server = _reload_server({})
        with patch("uvicorn.run", new=MagicMock()) as uvicorn_run:
            with patch.object(server.mcp, "run", new=MagicMock()):
                server.run_server()
        uvicorn_run.assert_not_called()

    def test_unknown_transport_falls_back_to_stdio(self):
        server = _reload_server({"MCP_TRANSPORT": "nonsense"})
        with patch.object(server.mcp, "run", new=MagicMock()) as run:
            server.run_server()
        run.assert_called_once_with()


class TestHttpAuthEnforcement:
    """The HTTP transports must refuse to start without bearer auth."""

    def test_refuses_to_start_without_auth(self):
        server = _reload_server({"MCP_TRANSPORT": "streamable-http"})
        with patch("uvicorn.run", new=MagicMock()) as uvicorn_run:
            with pytest.raises(SystemExit, match="bearer authentication is required"):
                server.run_server()
        uvicorn_run.assert_not_called()

    @pytest.mark.parametrize("transport", ["sse", "streamable-http"])
    def test_configured_tokens_allow_startup(self, transport):
        server = _reload_server({"MCP_TRANSPORT": transport, "MCP_AUTH_TOKENS": "client-token"})
        with patch("uvicorn.run", new=MagicMock()) as uvicorn_run:
            server.run_server()
        uvicorn_run.assert_called_once()

    def test_passthrough_mode_alone_allows_startup(self):
        server = _reload_server({"MCP_TRANSPORT": "streamable-http", "MCP_ALLOW_HA_TOKENS": "true"})
        with patch("uvicorn.run", new=MagicMock()) as uvicorn_run:
            server.run_server()
        uvicorn_run.assert_called_once()

    def test_auth_middleware_is_installed(self):
        from app.auth import BearerAuthMiddleware

        server = _reload_server(
            {"MCP_TRANSPORT": "streamable-http", "MCP_AUTH_TOKENS": "client-token"}
        )
        fake_app = MagicMock()
        with patch("uvicorn.run", new=MagicMock()):
            with patch.object(
                server.mcp, "streamable_http_app", new=MagicMock(return_value=fake_app)
            ):
                server.run_server()
        fake_app.add_middleware.assert_called_once_with(BearerAuthMiddleware)

    def test_stdio_needs_no_auth(self):
        """stdio has no request layer, so nothing is enforced."""
        server = _reload_server({})
        with patch.object(server.mcp, "run", new=MagicMock()) as run:
            server.run_server()
        run.assert_called_once_with()
