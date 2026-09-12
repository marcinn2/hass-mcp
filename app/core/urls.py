"""URL construction helpers for hass-mcp.

Identifiers reaching the API layer (entity IDs, automation IDs, tag IDs, ...) are
supplied by MCP callers and are interpolated into Home Assistant REST paths. Left
raw, a value such as ``../../api/config`` collapses the path during URL
normalization and redirects the request — carrying the long-lived admin token — to
an unintended endpoint. These helpers percent-encode identifiers so that a value
can only ever be a path segment, never a path.
"""

from urllib.parse import quote


def quote_segment(value: str) -> str:
    """
    Encode a value for safe use as a single URL path segment.

    Path separators, query and fragment delimiters are percent-encoded, so the
    result cannot traverse to another endpoint or append query parameters.
    Ordinary Home Assistant identifiers pass through unchanged.

    Args:
        value: The identifier to encode (e.g. an entity or automation ID)

    Returns:
        The percent-encoded segment

    Examples:
        >>> quote_segment("light.living_room")
        'light.living_room'
        >>> quote_segment("../../api/config")
        '..%2F..%2Fapi%2Fconfig'
    """
    return quote(str(value), safe="")


def quote_path(value: str) -> str:
    """
    Encode a value for safe use as a multi-segment URL path.

    Path separators are preserved, so this suits identifiers that are genuinely
    hierarchical (such as blueprint paths). Traversal is still neutralized by
    removing ``.`` and ``..`` segments.

    Args:
        value: The path to encode (e.g. "automation/motion_light.yaml")

    Returns:
        The percent-encoded path with traversal segments removed

    Examples:
        >>> quote_path("automation/homeassistant/motion_light.yaml")
        'automation/homeassistant/motion_light.yaml'
        >>> quote_path("../../api/config")
        'api/config'
    """
    segments = [seg for seg in str(value).split("/") if seg not in ("", ".", "..")]
    return "/".join(quote(seg, safe="") for seg in segments)
