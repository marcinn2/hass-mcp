"""Configuration for hass-mcp.

Settings are layered, with the configuration file acting as the baseline and
environment variables overriding it:

    built-in defaults  ->  config file  ->  environment variables

See app/config_file.py for the file format and search paths, and
config/hass-mcp.example.json for a documented example.
"""

import logging
import os
import os.path
from pathlib import Path

from app.config_file import CONFIG_FILE_PATH, resolve


def _split_tokens(value: object) -> list[str]:
    """
    Normalise a token list from either source.

    The configuration file provides a JSON array; environment variables provide
    a comma-separated string. Blank entries are dropped.

    Args:
        value: A list, a comma-separated string, or None

    Returns:
        The tokens, in order, with surrounding whitespace stripped
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [token.strip() for token in value.split(",") if token.strip()]
    if isinstance(value, (list, tuple)):
        return [str(token).strip() for token in value if str(token).strip()]
    return []


def _patterns_from(value: object, path: object = None) -> list[str]:
    """
    Build a policy pattern list from an inline list and an optional file.

    The file form (one pattern per line, "#" starts a comment) keeps long lists
    out of the configuration file, and is how the fork this came from expects
    large allowlists to be managed.

    Args:
        value: A list, a comma-separated string, or None
        path: Optional path to a newline-delimited pattern file

    Returns:
        The combined patterns, inline entries first
    """
    patterns = _split_tokens(value)

    if isinstance(path, str) and path.strip():
        try:
            for line in Path(path.strip()).read_text().splitlines():
                entry = line.split("#", 1)[0].strip()
                if entry:
                    patterns.append(entry)
        except OSError as e:
            logging.getLogger(__name__).error(f"Could not read pattern file {path!r}: {e}")

    return patterns


# Home Assistant configuration
HA_URL: str = resolve("home_assistant", "url", "HA_URL", "http://localhost:8123", cast="str")
HA_TOKEN: str = resolve("home_assistant", "token", "HA_TOKEN", "", cast="str")

# SSL/TLS Configuration
HA_SSL_VERIFY: str | bool = resolve("home_assistant", "ssl_verify", "HA_SSL_VERIFY", "true")

# Logging. INFO includes per-request lines naming entity IDs and search
# queries, which are personal data; WARNING drops those while keeping errors.
# See docs/privacy.md.
LOG_LEVEL: str = resolve("server", "log_level", "LOG_LEVEL", "INFO", cast="str").upper()

# Server / transport configuration
MCP_TRANSPORT: str = resolve("server", "transport", "MCP_TRANSPORT", "stdio", cast="str")
MCP_HOST: str = resolve("server", "host", "MCP_HOST", "127.0.0.1", cast="str")
# PORT is honoured as a fallback for Smithery compatibility; MCP_PORT wins.
MCP_PORT: int = int(
    os.environ.get(
        "MCP_PORT",
        os.environ.get("PORT", resolve("server", "port", None, 8000)),
    )
)

# MCP bearer authentication (HTTP transports only).
#
# Clients must present "Authorization: Bearer <token>". A token matching one of
# MCP_AUTH_TOKENS authenticates the client, and Home Assistant calls then use
# the configured HA token. When MCP_ALLOW_HA_TOKENS is enabled, a token that
# matches none of them is instead forwarded to Home Assistant as that request's
# HA token, letting each client bring its own credential.
MCP_AUTH_TOKENS: list[str] = _split_tokens(resolve("auth", "tokens", "MCP_AUTH_TOKENS", None))
MCP_ALLOW_HA_TOKENS: bool = resolve(
    "auth", "allow_ha_tokens", "MCP_ALLOW_HA_TOKENS", False, cast="bool"
)

# Exposed MCP tool surface.
#
# Empty means "the default set" (see app.tools.registry). A list selects exactly
# those tools; the single entry "all" selects every known tool. TOOLS_DISABLED
# is subtracted from whichever set results, so it can trim the default surface
# without restating it.
TOOLS_ENABLED: list[str] = _split_tokens(resolve("tools", "enabled", "MCP_TOOLS_ENABLED", None))
TOOLS_DISABLED: list[str] = _split_tokens(resolve("tools", "disabled", "MCP_TOOLS_DISABLED", None))

# Access policy. Every control here defaults to permissive: see app.core.policy
# for why this project inverts the posture of the fork the mechanism came from.
POLICY_READ_ONLY: bool = resolve("policy", "read_only", "HASS_MCP_READ_ONLY", False, cast="bool")
POLICY_ENTITY_ALLOWLIST: list[str] = _patterns_from(
    resolve("policy", "entity_allowlist", "HASS_MCP_ENTITY_ALLOWLIST", None),
    resolve("policy", "entity_allowlist_file", "HASS_MCP_ENTITY_ALLOWLIST_FILE", None),
)
POLICY_CONTROL_DENYLIST: list[str] = _patterns_from(
    resolve("policy", "control_denylist", "HASS_MCP_CONTROL_DENYLIST", None),
    resolve("policy", "control_denylist_file", "HASS_MCP_CONTROL_DENYLIST_FILE", None),
)

# Cache configuration
CACHE_ENABLED: bool = resolve("cache", "enabled", "HASS_MCP_CACHE_ENABLED", True, cast="bool")
CACHE_BACKEND: str = resolve(
    "cache", "backend", "HASS_MCP_CACHE_BACKEND", "memory", cast="str"
).lower()
CACHE_DEFAULT_TTL: int = resolve(
    "cache", "default_ttl", "HASS_MCP_CACHE_DEFAULT_TTL", 300, cast="int"
)
CACHE_MAX_SIZE: int = resolve("cache", "max_size", "HASS_MCP_CACHE_MAX_SIZE", 1000, cast="int")
REDIS_URL: str | None = resolve("cache", "redis_url", "HASS_MCP_CACHE_REDIS_URL", None)
CACHE_DIR: str = resolve("cache", "cache_dir", "HASS_MCP_CACHE_DIR", ".cache", cast="str")


def get_config_file_path() -> str | None:
    """
    Return the configuration file in use, if any.

    Returns:
        Path to the loaded configuration file, or None when running on
        defaults and environment variables alone
    """
    return str(CONFIG_FILE_PATH) if CONFIG_FILE_PATH else None


def get_ha_headers() -> dict:
    """
    Return the headers needed for Home Assistant API requests.

    The token comes from :func:`app.auth.resolve_ha_token`, which prefers a
    per-request bearer token (HTTP transports) and otherwise falls back to the
    configured ``HA_TOKEN``.
    """
    # Imported lazily: app.auth reads app.config at call time, and a module
    # level import here would be circular.
    from app.auth import resolve_ha_token  # noqa: PLC0415 - lazy: breaks import cycle

    headers = {
        "Content-Type": "application/json",
    }

    # Only add Authorization header if a token is available
    token = resolve_ha_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


def get_ssl_verify_value() -> str | bool:
    """
    Parse the HA_SSL_VERIFY setting into an httpx-compatible value.

    Returns:
        - True: Use system CA certificates (default)
        - False: Disable SSL verification (for self-signed certificates)
        - str: Path to custom CA certificate bundle

    Examples:
        HA_SSL_VERIFY="true" -> True (system CAs)
        HA_SSL_VERIFY="false" -> False (disable verification)
        HA_SSL_VERIFY="/path/to/ca.pem" -> "/path/to/ca.pem" (custom CA)
    """
    value = HA_SSL_VERIFY
    logger = logging.getLogger(__name__)

    if isinstance(value, str):
        lower_value = value.lower()
        if lower_value in ("true", "1", "yes"):
            return True
        if lower_value in ("false", "0", "no"):
            return False
        # Treat as file path - validate it exists
        if os.path.isfile(value):
            return value
        logger.warning(
            f"HA_SSL_VERIFY points to non-existent file: {value}. "
            f"Falling back to system CA certificates."
        )
        return True

    # A JSON boolean from the configuration file needs no parsing
    if isinstance(value, bool):
        return value

    # Default to True (system CAs)
    return True
