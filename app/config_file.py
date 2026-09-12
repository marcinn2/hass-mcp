"""Structured configuration file support for hass-mcp.

Configuration is layered. The file is the baseline; the environment overrides it:

    built-in defaults  ->  config file  ->  environment variables

A single JSON document describes the whole server, grouped into sections:

    {
      "home_assistant": {"url": "...", "token": "...", "ssl_verify": true},
      "server":         {"transport": "stdio", "host": "127.0.0.1", "port": 8000},
      "auth":           {"tokens": ["..."], "allow_ha_tokens": false},
      "tools":          {"enabled": ["..."], "disabled": ["..."]},
      "policy":         {"read_only": false, "entity_allowlist": [], ...},
      "cache":          {"enabled": true, "backend": "memory", ...},
      "vector_db":      {"enabled": false, "backend": "chroma", ...}
    }

The file is located from ``HASS_MCP_CONFIG_FILE`` when set, otherwise by
searching ``./``, ``./config/``, ``$HASS_MCP_CONFIG_DIR``, ``~/.hass-mcp/`` and
``/etc/hass-mcp/`` in that order. See ``config/hass-mcp.example.json`` for a
documented example. This module deliberately imports nothing from ``app`` so
that ``app.config`` can use it while remaining import-safe.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - pyyaml is a declared dependency
    yaml = None

logger = logging.getLogger(__name__)

# Environment variable holding an explicit path to the configuration file
CONFIG_FILE_ENV = "HASS_MCP_CONFIG_FILE"

# Environment variable holding a directory to search for the configuration file
CONFIG_DIR_ENV = "HASS_MCP_CONFIG_DIR"

# Recognised top-level sections. Anything else is reported as a likely typo.
KNOWN_SECTIONS = (
    "home_assistant",
    "server",
    "auth",
    "tools",
    "policy",
    "cache",
    "vector_db",
)

# File names searched for, in order, within each candidate directory
CONFIG_FILE_NAMES = (
    "hass-mcp.json",
    ".hass-mcp.json",
    "hass-mcp.yaml",
    "hass-mcp.yml",
)


class ConfigFileError(Exception):
    """Raised when a configuration file is missing, unreadable or malformed."""


def _strip_comments(value: Any) -> Any:
    """
    Recursively drop keys beginning with "$".

    JSON has no comment syntax, so "$comment" keys are used for documentation
    inside the configuration file and must not reach the settings.

    Args:
        value: Any decoded JSON value

    Returns:
        The value with "$"-prefixed keys removed throughout
    """
    if isinstance(value, dict):
        return {k: _strip_comments(v) for k, v in value.items() if not k.startswith("$")}
    if isinstance(value, list):
        return [_strip_comments(item) for item in value]
    return value


def _search_directories() -> list[Path]:
    """Return the directories searched for a configuration file, in order."""
    cwd = Path.cwd()
    # ./config is checked alongside the working directory so that a file copied
    # from config/hass-mcp.example.json is picked up where it sits.
    directories = [cwd, cwd / "config"]
    config_dir = os.environ.get(CONFIG_DIR_ENV)
    if config_dir:
        directories.append(Path(config_dir))
    directories.append(Path.home() / ".hass-mcp")
    directories.append(Path("/etc/hass-mcp"))
    return directories


def find_config_file() -> Path | None:
    """
    Locate the configuration file.

    An explicit ``HASS_MCP_CONFIG_FILE`` wins; a path set there that does not
    exist is an error rather than a silent fallback to defaults.

    Returns:
        Path to the configuration file, or None when no file is configured

    Raises:
        ConfigFileError: If HASS_MCP_CONFIG_FILE points at a missing file
    """
    explicit = os.environ.get(CONFIG_FILE_ENV)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigFileError(
                f"{CONFIG_FILE_ENV} points to {explicit!r}, which does not exist. "
                "Correct the path or unset the variable to use defaults."
            )
        return path

    for directory in _search_directories():
        for name in CONFIG_FILE_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate

    return None


def load_config_file(path: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    """
    Load and validate the configuration file.

    Args:
        path: Optional explicit path. When None, the file is located as usual.

    Returns:
        A (config_data, path) tuple. Both are empty/None when no file is present.

    Raises:
        ConfigFileError: If the file cannot be read or parsed
    """
    if path is None:
        path = find_config_file()
    if path is None:
        logger.debug("No hass-mcp configuration file found; using defaults and environment")
        return {}, None

    if not path.is_file():
        raise ConfigFileError(f"Configuration file {path} does not exist.")

    try:
        text = path.read_text()
    except OSError as e:
        raise ConfigFileError(f"Cannot read configuration file {path}: {e}") from e

    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            if yaml is None:  # pragma: no cover - pyyaml is a declared dependency
                raise ConfigFileError(
                    f"{path} is YAML but PyYAML is not installed. "
                    "Install pyyaml, or use JSON instead."
                )
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
    except ConfigFileError:
        raise
    except Exception as e:
        raise ConfigFileError(f"Configuration file {path} is not valid: {e}") from e

    if not isinstance(data, dict):
        raise ConfigFileError(
            f"Configuration file {path} must contain an object at the top level, "
            f"got {type(data).__name__}."
        )

    data = _strip_comments(data)

    unknown = [key for key in data if key not in KNOWN_SECTIONS]
    if unknown:
        logger.warning(
            "Ignoring unknown section(s) %s in %s. Known sections: %s",
            ", ".join(sorted(unknown)),
            path,
            ", ".join(KNOWN_SECTIONS),
        )

    logger.info("Loaded configuration from %s", path)
    return data, path


# Loaded once at import so every consumer sees the same baseline.
try:
    CONFIG_DATA, CONFIG_FILE_PATH = load_config_file()
except ConfigFileError:
    # Re-raised for the caller, but make the failure visible even if logging is
    # not yet configured by the time app.config is imported.
    logger.error("Failed to load hass-mcp configuration file", exc_info=True)
    raise


def get_section(name: str) -> dict[str, Any]:
    """
    Return a top-level section of the configuration file.

    Args:
        name: Section name (e.g. "cache")

    Returns:
        The section as a dict, or an empty dict when absent or malformed
    """
    section = CONFIG_DATA.get(name)
    if section is None:
        return {}
    if not isinstance(section, dict):
        logger.warning(
            "Configuration section %r must be an object, got %s; ignoring it",
            name,
            type(section).__name__,
        )
        return {}
    return section


def _as_bool(value: Any) -> bool:
    """Interpret a JSON or environment value as a boolean."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def resolve(
    section: str,
    key: str,
    env_var: str | None = None,
    default: Any = None,
    cast: str | None = None,
) -> Any:
    """
    Resolve one setting across the configuration layers.

    Precedence is environment variable, then configuration file, then default.

    Args:
        section: Top-level section name in the configuration file
        key: Key within that section
        env_var: Environment variable that overrides the file, if any
        default: Value used when neither source provides one
        cast: Optional coercion — "bool", "int", "float" or "str"

    Returns:
        The resolved value

    Examples:
        resolve("home_assistant", "url", "HA_URL", "http://localhost:8123")
        resolve("cache", "enabled", "HASS_MCP_CACHE_ENABLED", True, cast="bool")
    """
    raw: Any = None
    found = False

    if env_var:
        env_value = os.environ.get(env_var)
        if env_value is not None:
            raw, found = env_value, True

    if not found:
        file_section = get_section(section)
        if key in file_section:
            raw, found = file_section[key], True

    if not found:
        return default

    if cast == "bool":
        return _as_bool(raw)
    if cast == "int":
        return int(raw)
    if cast == "float":
        return float(raw)
    if cast == "str":
        return str(raw)
    return raw
