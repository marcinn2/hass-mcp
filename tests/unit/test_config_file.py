"""Tests for the structured configuration file.

The file is the baseline and the environment overrides it:

    built-in defaults  ->  config file  ->  environment variables
"""

import importlib
import json
import os
from pathlib import Path

import pytest

from app import config_file

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPO_ROOT / "config" / "hass-mcp.example.json"


def write_config(tmp_path, data, name="hass-mcp.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def reload_config(monkeypatch, config_path=None, env=None):
    """Reload the config chain with an optional config file and environment."""
    for var in (
        "HA_URL",
        "HA_TOKEN",
        "HA_SSL_VERIFY",
        "MCP_TRANSPORT",
        "MCP_HOST",
        "MCP_PORT",
        "PORT",
        "HASS_MCP_CACHE_ENABLED",
        "HASS_MCP_CACHE_BACKEND",
        "HASS_MCP_CACHE_DEFAULT_TTL",
        "HASS_MCP_CACHE_MAX_SIZE",
        "HASS_MCP_CACHE_REDIS_URL",
        "HASS_MCP_CACHE_DIR",
        "HASS_MCP_CONFIG_FILE",
        "HASS_MCP_CONFIG_DIR",
        "MCP_AUTH_TOKENS",
        "MCP_ALLOW_HA_TOKENS",
        "LOG_LEVEL",
        "MCP_STATELESS_HTTP",
        "MCP_JSON_RESPONSE",
        "MCP_SESSION_IDLE_TIMEOUT",
        "MCP_MAX_SESSIONS",
        "MCP_TOOLS_ENABLED",
        "MCP_TOOLS_DISABLED",
        "HASS_MCP_READ_ONLY",
        "HASS_MCP_ENTITY_ALLOWLIST",
        "HASS_MCP_ENTITY_ALLOWLIST_FILE",
        "HASS_MCP_CONTROL_DENYLIST",
        "HASS_MCP_CONTROL_DENYLIST_FILE",
    ):
        monkeypatch.delenv(var, raising=False)

    if config_path is not None:
        monkeypatch.setenv("HASS_MCP_CONFIG_FILE", str(config_path))
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, str(value))

    import app.config
    import app.config_file

    importlib.reload(app.config_file)
    return importlib.reload(app.config)


@pytest.fixture(autouse=True)
def _restore_config():
    """Restore the modules to a pristine environment after each test."""
    yield
    for var in ("HASS_MCP_CONFIG_FILE", "HASS_MCP_CONFIG_DIR"):
        os.environ.pop(var, None)
    import app.config
    import app.config_file

    importlib.reload(app.config_file)
    importlib.reload(app.config)


class TestExampleConfig:
    """The shipped example must be valid and describe real settings."""

    def test_example_is_valid_json(self):
        data = json.loads(EXAMPLE_CONFIG.read_text())
        assert isinstance(data, dict)

    def test_example_sections_are_all_recognised(self):
        data = json.loads(EXAMPLE_CONFIG.read_text())
        sections = [k for k in data if not k.startswith("$")]
        assert set(sections) == set(config_file.KNOWN_SECTIONS)

    def test_example_loads_as_the_baseline(self, monkeypatch):
        config = reload_config(monkeypatch, EXAMPLE_CONFIG)

        assert config.HA_URL == "http://homeassistant.local:8123"
        assert config.MCP_TRANSPORT == "stdio"
        assert config.MCP_PORT == 8000
        assert config.CACHE_BACKEND == "memory"
        assert config.CACHE_DEFAULT_TTL == 300
        assert config.get_config_file_path() == str(EXAMPLE_CONFIG)

    def test_example_endpoint_ttls_are_applied(self, monkeypatch):
        reload_config(monkeypatch, EXAMPLE_CONFIG)
        import app.core.cache.config as cache_config

        importlib.reload(cache_config)
        config = cache_config.CacheConfig()

        assert config.get_endpoint_ttl("entities", "get_state") == 60
        assert config.get_endpoint_ttl("entities", "get_entities") == 1800
        assert config.get_endpoint_ttl("entities") == 300
        assert config.get_endpoint_ttl("areas") == 3600

    def test_example_vector_db_section_is_applied(self, monkeypatch):
        reload_config(monkeypatch, EXAMPLE_CONFIG)
        import app.core.vectordb.config as vectordb_config

        importlib.reload(vectordb_config)
        config = vectordb_config.VectorDBConfig()

        assert config.is_enabled() is False
        assert config.get_backend() == "chroma"
        assert config.get_embedding_model_name() == "all-MiniLM-L6-v2"
        assert config.get_all_config()["search_similarity_threshold"] == 0.7
        assert config.get_all_config()["indexing_batch_size"] == 100


class TestFileIsBaseline:
    def test_home_assistant_section(self, monkeypatch, tmp_path):
        path = write_config(
            tmp_path,
            {"home_assistant": {"url": "http://ha.example:8123", "token": "abc123"}},
        )
        config = reload_config(monkeypatch, path)

        assert config.HA_URL == "http://ha.example:8123"
        assert config.HA_TOKEN == "abc123"
        assert config.get_ha_headers()["Authorization"] == "Bearer abc123"

    def test_server_section(self, monkeypatch, tmp_path):
        path = write_config(
            tmp_path,
            {"server": {"transport": "streamable-http", "host": "0.0.0.0", "port": 9000}},
        )
        config = reload_config(monkeypatch, path)

        assert config.MCP_TRANSPORT == "streamable-http"
        assert config.MCP_HOST == "0.0.0.0"
        assert config.MCP_PORT == 9000

    def test_cache_section(self, monkeypatch, tmp_path):
        path = write_config(
            tmp_path,
            {
                "cache": {
                    "enabled": False,
                    "backend": "file",
                    "default_ttl": 77,
                    "max_size": 55,
                    "cache_dir": "/tmp/hass-cache",
                }
            },
        )
        config = reload_config(monkeypatch, path)

        assert config.CACHE_ENABLED is False
        assert config.CACHE_BACKEND == "file"
        assert config.CACHE_DEFAULT_TTL == 77
        assert config.CACHE_MAX_SIZE == 55
        assert config.CACHE_DIR == "/tmp/hass-cache"

    def test_ssl_verify_accepts_json_booleans(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"home_assistant": {"ssl_verify": False}})
        config = reload_config(monkeypatch, path)

        assert config.get_ssl_verify_value() is False

    def test_omitted_values_fall_back_to_defaults(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"cache": {"default_ttl": 42}})
        config = reload_config(monkeypatch, path)

        assert config.CACHE_DEFAULT_TTL == 42
        assert config.HA_URL == "http://localhost:8123"
        assert config.CACHE_BACKEND == "memory"
        assert config.CACHE_MAX_SIZE == 1000

    def test_no_file_uses_defaults(self, monkeypatch):
        config = reload_config(monkeypatch)

        assert config.get_config_file_path() is None
        assert config.HA_URL == "http://localhost:8123"
        assert config.CACHE_BACKEND == "memory"


class TestEnvironmentOverridesFile:
    @pytest.mark.parametrize(
        ("env_var", "env_value", "attribute", "expected"),
        [
            ("HA_URL", "http://env:8123", "HA_URL", "http://env:8123"),
            ("HA_TOKEN", "env-token", "HA_TOKEN", "env-token"),
            ("MCP_TRANSPORT", "sse", "MCP_TRANSPORT", "sse"),
            ("MCP_HOST", "10.0.0.1", "MCP_HOST", "10.0.0.1"),
            ("MCP_PORT", "9443", "MCP_PORT", 9443),
            ("HASS_MCP_CACHE_BACKEND", "redis", "CACHE_BACKEND", "redis"),
            ("HASS_MCP_CACHE_DEFAULT_TTL", "60", "CACHE_DEFAULT_TTL", 60),
            ("HASS_MCP_CACHE_MAX_SIZE", "7", "CACHE_MAX_SIZE", 7),
            ("HASS_MCP_CACHE_ENABLED", "false", "CACHE_ENABLED", False),
        ],
    )
    def test_env_wins(self, monkeypatch, tmp_path, env_var, env_value, attribute, expected):
        path = write_config(
            tmp_path,
            {
                "home_assistant": {"url": "http://file:8123", "token": "file-token"},
                "server": {"transport": "stdio", "host": "127.0.0.1", "port": 8000},
                "cache": {
                    "enabled": True,
                    "backend": "memory",
                    "default_ttl": 300,
                    "max_size": 1000,
                },
            },
        )
        config = reload_config(monkeypatch, path, {env_var: env_value})

        assert getattr(config, attribute) == expected

    def test_port_fallback_is_used_when_mcp_port_absent(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"server": {"port": 8000}})
        config = reload_config(monkeypatch, path, {"PORT": "7777"})

        assert config.MCP_PORT == 7777

    def test_mcp_port_beats_port_and_file(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"server": {"port": 8000}})
        config = reload_config(monkeypatch, path, {"PORT": "7777", "MCP_PORT": "9001"})

        assert config.MCP_PORT == 9001

    def test_env_overrides_cache_endpoint_scalars_not_the_map(self, monkeypatch, tmp_path):
        """Per-endpoint TTLs have no env equivalent, so the file stays authoritative."""
        path = write_config(
            tmp_path,
            {"cache": {"default_ttl": 300, "endpoints": {"areas": 3600}}},
        )
        reload_config(monkeypatch, path, {"HASS_MCP_CACHE_DEFAULT_TTL": "60"})
        import app.core.cache.config as cache_config

        importlib.reload(cache_config)
        config = cache_config.CacheConfig()

        assert config.get_default_ttl() == 60
        assert config.get_endpoint_ttl("areas") == 3600


class TestDiscovery:
    def test_config_dir_is_searched(self, monkeypatch, tmp_path):
        write_config(tmp_path, {"cache": {"default_ttl": 123}})
        monkeypatch.delenv("HASS_MCP_CONFIG_FILE", raising=False)
        monkeypatch.setenv("HASS_MCP_CONFIG_DIR", str(tmp_path))

        import app.config
        import app.config_file

        importlib.reload(app.config_file)
        config = importlib.reload(app.config)

        assert config.CACHE_DEFAULT_TTL == 123

    def test_dot_prefixed_name_is_found(self, monkeypatch, tmp_path):
        write_config(tmp_path, {"cache": {"default_ttl": 321}}, name=".hass-mcp.json")
        monkeypatch.delenv("HASS_MCP_CONFIG_FILE", raising=False)
        monkeypatch.setenv("HASS_MCP_CONFIG_DIR", str(tmp_path))

        import app.config
        import app.config_file

        importlib.reload(app.config_file)
        config = importlib.reload(app.config)

        assert config.CACHE_DEFAULT_TTL == 321

    def test_yaml_is_accepted(self, monkeypatch, tmp_path):
        path = tmp_path / "hass-mcp.yaml"
        path.write_text("cache:\n  default_ttl: 99\n")
        config = reload_config(monkeypatch, path)

        assert config.CACHE_DEFAULT_TTL == 99


class TestErrors:
    def test_missing_explicit_path_is_an_error(self, tmp_path):
        with pytest.raises(config_file.ConfigFileError, match="does not exist"):
            config_file.load_config_file(tmp_path / "nope.json")

    def test_missing_env_path_is_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HASS_MCP_CONFIG_FILE", str(tmp_path / "nope.json"))

        with pytest.raises(config_file.ConfigFileError, match="does not exist"):
            config_file.load_config_file()

    def test_malformed_json_is_an_error(self, tmp_path):
        path = tmp_path / "hass-mcp.json"
        path.write_text('{"cache": {"default_ttl": 1,}}')

        with pytest.raises(config_file.ConfigFileError, match="is not valid"):
            config_file.load_config_file(path)

    def test_non_object_top_level_is_an_error(self, tmp_path):
        path = tmp_path / "hass-mcp.json"
        path.write_text("[1, 2, 3]")

        with pytest.raises(config_file.ConfigFileError, match="must contain an object"):
            config_file.load_config_file(path)

    def test_unknown_sections_are_ignored_with_a_warning(self, tmp_path, caplog):
        path = tmp_path / "hass-mcp.json"
        path.write_text('{"home_asistant": {"url": "x"}, "cache": {"default_ttl": 5}}')

        with caplog.at_level("WARNING"):
            data, _ = config_file.load_config_file(path)

        assert "home_asistant" in caplog.text
        assert data["cache"]["default_ttl"] == 5

    def test_non_object_section_is_ignored(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"cache": "not-an-object"})
        config = reload_config(monkeypatch, path)

        assert config.CACHE_BACKEND == "memory"


class TestCommentStripping:
    def test_dollar_keys_are_removed_recursively(self):
        data = {
            "$comment": "ignore me",
            "cache": {"$comment": ["a", "b"], "default_ttl": 5, "endpoints": {"$c": 1, "areas": 2}},
        }

        assert config_file._strip_comments(data) == {
            "cache": {"default_ttl": 5, "endpoints": {"areas": 2}}
        }

    def test_comments_do_not_reach_settings(self, monkeypatch, tmp_path):
        path = write_config(
            tmp_path,
            {
                "cache": {
                    "$comment": "hi",
                    "default_ttl": 11,
                    "endpoints": {"$note": "x", "areas": 9},
                }
            },
        )
        reload_config(monkeypatch, path)
        import app.core.cache.config as cache_config

        importlib.reload(cache_config)
        config = cache_config.CacheConfig()

        assert config.get_default_ttl() == 11
        assert config.get_endpoint_ttl("areas") == 9
        assert all(not key.startswith("$") for key in config._endpoint_ttls)


class TestAuthSection:
    """The auth section feeds MCP bearer authentication."""

    def test_tokens_and_flag_from_file(self, monkeypatch, tmp_path):
        path = write_config(
            tmp_path,
            {"auth": {"tokens": ["one", "two"], "allow_ha_tokens": True}},
        )
        config = reload_config(monkeypatch, path)

        assert config.MCP_AUTH_TOKENS == ["one", "two"]
        assert config.MCP_ALLOW_HA_TOKENS is True

    def test_defaults_are_closed(self, monkeypatch):
        config = reload_config(monkeypatch)

        assert config.MCP_AUTH_TOKENS == []
        assert config.MCP_ALLOW_HA_TOKENS is False

    def test_env_overrides_file_tokens(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"auth": {"tokens": ["from-file"]}})
        config = reload_config(monkeypatch, path, {"MCP_AUTH_TOKENS": "from-env"})

        assert config.MCP_AUTH_TOKENS == ["from-env"]

    def test_env_comma_separated_list_is_split(self, monkeypatch):
        config = reload_config(monkeypatch, None, {"MCP_AUTH_TOKENS": " a , b ,, c "})

        assert config.MCP_AUTH_TOKENS == ["a", "b", "c"]

    def test_env_overrides_allow_ha_tokens(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"auth": {"allow_ha_tokens": False}})
        config = reload_config(monkeypatch, path, {"MCP_ALLOW_HA_TOKENS": "true"})

        assert config.MCP_ALLOW_HA_TOKENS is True

    def test_auth_is_a_recognised_section(self):
        assert "auth" in config_file.KNOWN_SECTIONS


class TestToolsSection:
    """The tools section controls which MCP tools are exposed."""

    def test_enabled_and_disabled_from_file(self, monkeypatch, tmp_path):
        path = write_config(
            tmp_path,
            {"tools": {"enabled": ["get_entity", "list_items"], "disabled": ["list_items"]}},
        )
        config = reload_config(monkeypatch, path)

        assert config.TOOLS_ENABLED == ["get_entity", "list_items"]
        assert config.TOOLS_DISABLED == ["list_items"]

    def test_defaults_are_empty(self, monkeypatch):
        config = reload_config(monkeypatch)

        assert config.TOOLS_ENABLED == []
        assert config.TOOLS_DISABLED == []

    def test_env_overrides_file(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"tools": {"enabled": ["from-file"]}})
        config = reload_config(monkeypatch, path, {"MCP_TOOLS_ENABLED": "get_entity"})

        assert config.TOOLS_ENABLED == ["get_entity"]

    def test_env_list_is_comma_separated(self, monkeypatch):
        config = reload_config(monkeypatch, None, {"MCP_TOOLS_DISABLED": " restart_ha , diagnose "})

        assert config.TOOLS_DISABLED == ["restart_ha", "diagnose"]

    def test_tools_is_a_recognised_section(self):
        assert "tools" in config_file.KNOWN_SECTIONS


class TestPolicySection:
    """The policy section is permissive by default; the fork it came from is not."""

    def test_defaults_are_permissive(self, monkeypatch):
        config = reload_config(monkeypatch)

        assert config.POLICY_READ_ONLY is False
        assert config.POLICY_ENTITY_ALLOWLIST == []
        assert config.POLICY_CONTROL_DENYLIST == []

    def test_values_from_file(self, monkeypatch, tmp_path):
        path = write_config(
            tmp_path,
            {
                "policy": {
                    "read_only": True,
                    "entity_allowlist": ["light.*"],
                    "control_denylist": ["lock.*"],
                }
            },
        )
        config = reload_config(monkeypatch, path)

        assert config.POLICY_READ_ONLY is True
        assert config.POLICY_ENTITY_ALLOWLIST == ["light.*"]
        assert config.POLICY_CONTROL_DENYLIST == ["lock.*"]

    def test_env_overrides_file(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"policy": {"read_only": False}})
        config = reload_config(monkeypatch, path, {"HASS_MCP_READ_ONLY": "true"})

        assert config.POLICY_READ_ONLY is True

    def test_patterns_can_come_from_a_file(self, monkeypatch, tmp_path):
        patterns = tmp_path / "allow.txt"
        patterns.write_text("light.kitchen\n# a comment\n\nsensor.gpu_*  # inline\n")
        config = reload_config(
            monkeypatch,
            None,
            {
                "HASS_MCP_ENTITY_ALLOWLIST": "lock.front",
                "HASS_MCP_ENTITY_ALLOWLIST_FILE": str(patterns),
            },
        )

        # Inline entries first, then the file's, with comments stripped
        assert config.POLICY_ENTITY_ALLOWLIST == ["lock.front", "light.kitchen", "sensor.gpu_*"]

    def test_missing_pattern_file_is_logged_not_fatal(self, monkeypatch, tmp_path, caplog):
        with caplog.at_level("ERROR"):
            config = reload_config(
                monkeypatch,
                None,
                {"HASS_MCP_ENTITY_ALLOWLIST_FILE": str(tmp_path / "nope.txt")},
            )

        assert config.POLICY_ENTITY_ALLOWLIST == []

    def test_policy_is_a_recognised_section(self):
        assert "policy" in config_file.KNOWN_SECTIONS

    def test_example_config_ships_permissive(self):
        import json

        data = json.loads(EXAMPLE_CONFIG.read_text())["policy"]

        assert data["read_only"] is False
        assert data["entity_allowlist"] == []
        assert data["control_denylist"] == []

    def test_example_config_documents_the_fork_proposal(self):
        import json

        data = json.loads(EXAMPLE_CONFIG.read_text())["policy"]
        proposal = "\n".join(data["$comment_fork_proposal"])

        assert "paultanger" in proposal
        assert "read_only" in proposal
        assert "FAIL-CLOSED" in proposal.upper()


class TestLogLevel:
    """LOG_LEVEL was documented but never read; INFO logs personal data."""

    def test_defaults_to_info(self, monkeypatch):
        config = reload_config(monkeypatch)

        assert config.LOG_LEVEL == "INFO"

    @pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR"])
    def test_env_sets_the_level(self, monkeypatch, level):
        config = reload_config(monkeypatch, None, {"LOG_LEVEL": level})

        assert level == config.LOG_LEVEL

    def test_value_is_upper_cased(self, monkeypatch):
        config = reload_config(monkeypatch, None, {"LOG_LEVEL": "warning"})

        assert config.LOG_LEVEL == "WARNING"

    def test_comes_from_the_config_file_too(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"server": {"log_level": "WARNING"}})
        config = reload_config(monkeypatch, path)

        assert config.LOG_LEVEL == "WARNING"

    def test_env_overrides_the_file(self, monkeypatch, tmp_path):
        path = write_config(tmp_path, {"server": {"log_level": "DEBUG"}})
        config = reload_config(monkeypatch, path, {"LOG_LEVEL": "ERROR"})

        assert config.LOG_LEVEL == "ERROR"

    def test_it_is_actually_applied_to_logging(self, monkeypatch):
        """Regression: the setting existed in docs but changed nothing."""
        import importlib
        import logging

        reload_config(monkeypatch, None, {"LOG_LEVEL": "WARNING"})
        import app.server

        importlib.reload(app.server)
        try:
            assert logging.getLogger().getEffectiveLevel() == logging.WARNING
        finally:
            # Restore INFO so later tests see the usual level
            reload_config(monkeypatch)
            importlib.reload(app.server)

    def test_unknown_level_falls_back_to_info(self, monkeypatch):
        """A typo must not crash startup or silently disable logging."""
        import importlib
        import logging

        reload_config(monkeypatch, None, {"LOG_LEVEL": "NONSENSE"})
        import app.server

        importlib.reload(app.server)
        try:
            assert logging.getLogger().getEffectiveLevel() == logging.INFO
        finally:
            reload_config(monkeypatch)
            importlib.reload(app.server)


class TestSessionSettings:
    """Session behaviour for the HTTP transports, exposed via the server section."""

    def test_defaults_match_fastmcp(self, monkeypatch):
        """Defaults must mirror FastMCP's, so exposing them changes nothing."""
        config = reload_config(monkeypatch)

        assert config.MCP_STATELESS_HTTP is False
        assert config.MCP_JSON_RESPONSE is False
        assert config.MCP_SESSION_IDLE_TIMEOUT == 1800.0
        assert config.MCP_MAX_SESSIONS == 10000

    def test_values_from_file(self, monkeypatch, tmp_path):
        path = write_config(
            tmp_path,
            {
                "server": {
                    "stateless_http": True,
                    "json_response": True,
                    "session_idle_timeout": 600,
                    "max_sessions": 50,
                }
            },
        )
        config = reload_config(monkeypatch, path)

        assert config.MCP_STATELESS_HTTP is True
        assert config.MCP_JSON_RESPONSE is True
        assert config.MCP_SESSION_IDLE_TIMEOUT == 600.0
        assert config.MCP_MAX_SESSIONS == 50

    @pytest.mark.parametrize(
        ("env", "attr", "value", "expected"),
        [
            ("MCP_STATELESS_HTTP", "MCP_STATELESS_HTTP", "true", True),
            ("MCP_JSON_RESPONSE", "MCP_JSON_RESPONSE", "true", True),
            ("MCP_SESSION_IDLE_TIMEOUT", "MCP_SESSION_IDLE_TIMEOUT", "900", 900.0),
            ("MCP_MAX_SESSIONS", "MCP_MAX_SESSIONS", "25", 25),
        ],
    )
    def test_env_overrides_file(self, monkeypatch, tmp_path, env, attr, value, expected):
        path = write_config(
            tmp_path,
            {
                "server": {
                    "stateless_http": False,
                    "json_response": False,
                    "session_idle_timeout": 1800,
                    "max_sessions": 10000,
                }
            },
        )
        config = reload_config(monkeypatch, path, {env: value})

        assert getattr(config, attr) == expected

    def test_timeout_accepts_a_float(self, monkeypatch):
        config = reload_config(monkeypatch, None, {"MCP_SESSION_IDLE_TIMEOUT": "90.5"})

        assert config.MCP_SESSION_IDLE_TIMEOUT == 90.5

    def test_settings_reach_the_fastmcp_instance(self, monkeypatch):
        """Regression: these must be constructor kwargs — the session manager is
        built from mcp.settings when streamable_http_app() is first called, so
        setting them afterwards has no effect."""
        import importlib

        reload_config(
            monkeypatch,
            None,
            {"MCP_STATELESS_HTTP": "true", "MCP_MAX_SESSIONS": "7"},
        )
        import app.server

        importlib.reload(app.server)
        try:
            assert app.server.mcp.settings.stateless_http is True
            assert app.server.mcp.settings.max_sessions == 7
        finally:
            reload_config(monkeypatch)
            importlib.reload(app.server)

    def test_example_config_documents_the_defaults(self):
        import json

        server = json.loads(EXAMPLE_CONFIG.read_text())["server"]

        assert server["stateless_http"] is False
        assert server["json_response"] is False
        assert server["session_idle_timeout"] == 1800
        assert server["max_sessions"] == 10000
