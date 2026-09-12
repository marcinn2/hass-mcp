"""Statistics API module for hass-mcp.

This module provides functions for calculating statistics and analyzing usage patterns.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.api.entities import get_entities, get_entity_history, parse_iso_datetime
from app.api.logbook import get_entity_logbook
from app.core import policy
from app.core.decorators import handle_api_errors
from app.core.ws import call_ws

logger = logging.getLogger(__name__)


# NOTE: This function is explicitly excluded from caching (US-006)
# Statistics are derived from history data and are highly dynamic, so they should not be cached
@handle_api_errors
async def get_entity_statistics(entity_id: str, period_days: int = 7) -> dict[str, Any]:
    """
    Get statistics for an entity (min, max, mean, median).

    Args:
        entity_id: The entity ID to get statistics for
        period_days: Number of days to analyze (default: 7)

    Returns:
        Dictionary containing:
        - entity_id: The entity ID analyzed
        - period_days: Number of days analyzed
        - data_points: Number of numeric data points found
        - statistics: Dictionary with min, max, mean, median values
        - note: Note if no data or entity is not numeric

    Example response:
        {
            "entity_id": "sensor.temperature",
            "period_days": 7,
            "data_points": 1680,
            "statistics": {
                "min": 18.5,
                "max": 24.3,
                "mean": 21.4,
                "median": 21.2
            }
        }

    Note:
        This calculates statistics from entity history data.
        Only numeric entities can provide meaningful statistics.
        Returns empty statistics if entity is not numeric or has no data.

    Best Practices:
        - Use for sensors with numeric values (temperature, humidity, etc.)
        - Keep period_days reasonable (7-30) for performance
        - Check for empty statistics if entity is not numeric
    """
    # Policy: refuse when the entity is outside the configured lists.
    if (refusal := policy.check_read(entity_id)) is not None:
        return refusal

    # Get history for the period
    history = await get_entity_history(entity_id, hours=period_days * 24)

    # Check for errors
    if isinstance(history, dict) and "error" in history:
        return {
            "entity_id": entity_id,
            "period_days": period_days,
            "error": history["error"],
            "statistics": {},
        }

    # Flatten history list (history is a list of lists)
    states = []
    if isinstance(history, list):
        for state_list in history:
            if isinstance(state_list, list):
                states.extend(state_list)

    if not states:
        return {
            "entity_id": entity_id,
            "period_days": period_days,
            "note": "No data available for the specified period",
            "statistics": {},
        }

    # Extract numeric values
    numeric_values = []
    for state in states:
        state_value = state.get("state")
        try:
            numeric_value = float(state_value)
            numeric_values.append(numeric_value)
        except (ValueError, TypeError):
            continue

    if not numeric_values:
        return {
            "entity_id": entity_id,
            "period_days": period_days,
            "note": "Entity state is not numeric, cannot calculate statistics",
            "statistics": {},
        }

    # Calculate statistics
    sorted_values = sorted(numeric_values)
    n = len(numeric_values)
    median = (
        sorted_values[n // 2]
        if n % 2 == 1
        else (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2
    )

    stats = {
        "entity_id": entity_id,
        "period_days": period_days,
        "data_points": len(numeric_values),
        "statistics": {
            "min": min(numeric_values),
            "max": max(numeric_values),
            "mean": sum(numeric_values) / len(numeric_values),
            "median": median,
        },
    }

    return stats


# NOTE: This function is explicitly excluded from caching (US-006)
# Statistics are derived from history data and are highly dynamic, so they should not be cached
@handle_api_errors
async def get_domain_statistics(domain: str, period_days: int = 7) -> dict[str, Any]:
    """
    Get aggregate statistics for all entities in a domain.

    Args:
        domain: The domain to get statistics for (e.g., 'sensor', 'light')
        period_days: Number of days to analyze (default: 7)

    Returns:
        Dictionary containing:
        - domain: The domain analyzed
        - period_days: Number of days analyzed
        - total_entities: Total number of entities in the domain
        - entity_statistics: Dictionary mapping entity_id to statistics

    Example response:
        {
            "domain": "sensor",
            "period_days": 7,
            "total_entities": 25,
            "entity_statistics": {
                "sensor.temperature": {
                    "min": 18.5,
                    "max": 24.3,
                    "mean": 21.4,
                    "median": 21.2
                }
            }
        }

    Note:
        This aggregates statistics for entities in a domain.
        Limited to first 10 entities for performance.
        Only numeric entities are included in entity_statistics.

    Best Practices:
        - Use for domains with numeric entities (sensor, energy, etc.)
        - Keep period_days reasonable (7-30) for performance
        - Consider using get_entity_statistics for individual entities
    """
    # Get all entities in domain
    entities = await get_entities(domain=domain, lean=True)

    # Check for errors
    if isinstance(entities, dict) and "error" in entities:
        return {
            "domain": domain,
            "period_days": period_days,
            "error": entities["error"],
            "total_entities": 0,
            "entity_statistics": {},
        }

    stats: dict[str, Any] = {
        "domain": domain,
        "period_days": period_days,
        "total_entities": len(entities),
        "entity_statistics": {},
    }

    # Get statistics for each entity (limited to first 10 for performance)
    for entity in entities[:10]:
        entity_id = entity.get("entity_id")
        if not entity_id:
            continue
        try:
            entity_stats = await get_entity_statistics(entity_id, period_days)
            # Only include entities with valid statistics
            if entity_stats.get("statistics"):
                stats["entity_statistics"][entity_id] = entity_stats.get("statistics", {})
        except Exception:  # nosec B112
            continue

    return stats


# NOTE: This function is explicitly excluded from caching (US-006)
# Usage patterns are derived from logbook data and are highly dynamic, so they should not be cached
@handle_api_errors
async def analyze_usage_patterns(entity_id: str, days: int = 30) -> dict[str, Any]:
    """
    Analyze usage patterns (when device is used most).

    Args:
        entity_id: The entity ID to analyze
        days: Number of days to analyze (default: 30)

    Returns:
        Dictionary containing:
        - entity_id: The entity ID analyzed
        - period_days: Number of days analyzed
        - total_events: Total number of logbook events
        - hourly_distribution: Dictionary mapping hour (0-23) to event count
        - daily_distribution: Dictionary mapping day name to event count
        - peak_hour: Hour with most events (0-23)
        - peak_day: Day of week with most events

    Example response:
        {
            "entity_id": "light.living_room",
            "period_days": 30,
            "total_events": 150,
            "hourly_distribution": {18: 30, 19: 25, 20: 20, ...},
            "daily_distribution": {"Monday": 25, "Tuesday": 22, ...},
            "peak_hour": 18,
            "peak_day": "Monday"
        }

    Note:
        This analyzes logbook entries to find usage patterns.
        Useful for understanding when devices are used most.
        Helps optimize automations based on actual usage.

    Best Practices:
        - Use for devices with frequent state changes (lights, switches, etc.)
        - Keep days reasonable (7-30) for performance
        - Use results to optimize automation schedules
    """
    # Get logbook entries
    logbook = await get_entity_logbook(entity_id, hours=days * 24)

    # Check for errors
    if (
        isinstance(logbook, list)
        and logbook
        and isinstance(logbook[0], dict)
        and "error" in logbook[0]
    ):
        return {
            "entity_id": entity_id,
            "period_days": days,
            "error": logbook[0].get("error"),
            "total_events": 0,
            "hourly_distribution": {},
            "daily_distribution": {},
            "peak_hour": None,
            "peak_day": None,
        }

    # Analyze by hour of day
    hourly_usage: dict[int, int] = {}
    daily_usage: dict[str, int] = {}

    for entry in logbook:
        when = entry.get("when")
        if when:
            try:
                # Parse timestamp (handle both with and without timezone)
                if when.endswith("Z"):
                    dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(when)

                hour = dt.hour
                hourly_usage[hour] = hourly_usage.get(hour, 0) + 1

                # Analyze by day of week
                day = dt.strftime("%A")
                daily_usage[day] = daily_usage.get(day, 0) + 1
            except (ValueError, AttributeError):  # nosec B110
                continue

    # Find peak hour and day
    peak_hour = max(hourly_usage.items(), key=lambda x: x[1])[0] if hourly_usage else None
    peak_day = max(daily_usage.items(), key=lambda x: x[1])[0] if daily_usage else None

    return {
        "entity_id": entity_id,
        "period_days": days,
        "total_events": len(logbook),
        "hourly_distribution": hourly_usage,
        "daily_distribution": daily_usage,
        "peak_hour": peak_hour,
        "peak_day": peak_day,
    }


# Aggregation buckets Home Assistant's recorder supports.
STATISTICS_PERIODS = ("5minute", "hour", "day", "week", "month")


@handle_api_errors
async def get_entity_statistics_range(
    entity_id: str,
    start_time: str | datetime,
    end_time: str | datetime | None = None,
    period: str = "hour",
) -> dict[str, Any]:
    """
    Get Home Assistant's long-term statistics for an entity over a date range.

    Ported from the mstump/hass-mcp fork. This queries
    ``recorder/statistics_during_period`` over the WebSocket API, which is the
    only way to reach long-term statistics: the REST API has no equivalent.

    Unlike get_entity_statistics, which derives values from raw state history,
    these statistics are pre-aggregated by Home Assistant and survive the
    recorder's short-term purge window (10 days by default), so they work for
    months or years of data.

    The entity must have a ``state_class`` that Home Assistant records as
    statistics (for example ``measurement`` or ``total_increasing``).

    Args:
        entity_id: The entity to fetch statistics for
        start_time: ISO-8601 string or datetime; treated as UTC when naive
        end_time: ISO-8601 string or datetime; defaults to now (UTC)
        period: Aggregation bucket - one of "5minute", "hour", "day", "week",
                "month" (default: "hour")

    Returns:
        Dictionary with entity_id, period, start_time, end_time and statistics.
        Each statistics entry carries start/end plus mean/min/max and,
        depending on the entity, sum/state.

    Raises:
        ValueError: If period is unknown or start_time is not before end_time

    Examples:
        await get_entity_statistics_range("sensor.power", "2026-01-01", period="day")
    """
    # Policy: refuse when the entity is outside the configured lists.
    if (refusal := policy.check_read(entity_id)) is not None:
        return refusal

    if period not in STATISTICS_PERIODS:
        raise ValueError(f"period must be one of {list(STATISTICS_PERIODS)}, got {period!r}")

    start_dt = parse_iso_datetime(start_time)
    end_dt = parse_iso_datetime(end_time) if end_time is not None else datetime.now(UTC)
    if start_dt >= end_dt:
        raise ValueError("start_time must be before end_time")

    start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    result = await call_ws(
        "recorder/statistics_during_period",
        start_time=start_iso,
        end_time=end_iso,
        statistic_ids=[entity_id],
        period=period,
    )

    # HA returns {entity_id: [points...]}; flatten when only one was requested.
    statistics = result.get(entity_id, []) if isinstance(result, dict) else result

    return {
        "entity_id": entity_id,
        "period": period,
        "start_time": start_iso,
        "end_time": end_iso,
        "count": len(statistics) if isinstance(statistics, list) else 0,
        "statistics": statistics,
    }


async def get_long_term_statistics(
    entity_id: str,
    hours: int = 24,
    period: str = "hour",
) -> dict[str, Any]:
    """
    Get long-term statistics for the last N hours.

    Convenience wrapper around get_entity_statistics_range.

    Args:
        entity_id: The entity to fetch statistics for
        hours: How far back from now (UTC) to query (default: 24)
        period: Aggregation bucket (default: "hour")

    Returns:
        Same shape as get_entity_statistics_range

    Examples:
        await get_long_term_statistics("sensor.power", hours=720, period="day")
    """
    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(hours=hours)
    return await get_entity_statistics_range(entity_id, start_dt, end_dt, period)
