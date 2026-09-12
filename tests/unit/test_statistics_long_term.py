"""Tests for long-term statistics and date-range history.

Ported alongside the features from the mstump/hass-mcp fork.

Long-term statistics come from Home Assistant's recorder over the WebSocket
API, so they survive the short-term purge window that limits the existing
history-derived get_entity_statistics.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.entities import get_entity_history_range, parse_iso_datetime
from app.api.statistics import (
    STATISTICS_PERIODS,
    get_entity_statistics_range,
    get_long_term_statistics,
)


@pytest.fixture(autouse=True)
def _token():
    """handle_api_errors short-circuits without a token, before the body runs."""
    with patch("app.core.decorators.HA_TOKEN", "test-token"):
        yield


SAMPLE_POINTS = [
    {"start": "2026-01-01T00:00:00Z", "mean": 21.5, "min": 20.0, "max": 23.0},
    {"start": "2026-01-01T01:00:00Z", "mean": 22.0, "min": 21.0, "max": 23.5},
]


class TestParseIsoDatetime:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2026-01-15", "2026-01-15T00:00:00+00:00"),
            ("2026-01-15T12:00:00", "2026-01-15T12:00:00+00:00"),
            ("2026-01-15T12:00:00Z", "2026-01-15T12:00:00+00:00"),
            ("2026-01-15T12:00:00+02:00", "2026-01-15T12:00:00+02:00"),
        ],
    )
    def test_parses_iso_strings(self, value, expected):
        assert parse_iso_datetime(value).isoformat() == expected

    def test_naive_datetime_becomes_utc(self):
        assert parse_iso_datetime(datetime(2026, 1, 15, 12)).tzinfo is UTC

    def test_aware_datetime_is_preserved(self):
        original = datetime(2026, 1, 15, 12, tzinfo=UTC)
        assert parse_iso_datetime(original) is original

    @pytest.mark.parametrize("value", [123, None, [], {}])
    def test_rejects_other_types(self, value):
        with pytest.raises(ValueError, match="must be str or datetime"):
            parse_iso_datetime(value)

    def test_rejects_unparseable_string(self):
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            parse_iso_datetime("not a date")


class TestGetEntityStatisticsRange:
    async def test_queries_recorder_over_websocket(self):
        call_ws = AsyncMock(return_value={"sensor.power": SAMPLE_POINTS})
        with patch("app.api.statistics.call_ws", call_ws):
            result = await get_entity_statistics_range(
                "sensor.power", "2026-01-01", "2026-01-02", period="day"
            )

        # The WebSocket message must be the long-term statistics one
        assert call_ws.await_args.args[0] == "recorder/statistics_during_period"
        kwargs = call_ws.await_args.kwargs
        assert kwargs["statistic_ids"] == ["sensor.power"]
        assert kwargs["period"] == "day"
        assert kwargs["start_time"] == "2026-01-01T00:00:00Z"
        assert kwargs["end_time"] == "2026-01-02T00:00:00Z"

        assert result["entity_id"] == "sensor.power"
        assert result["period"] == "day"
        assert result["count"] == 2
        assert result["statistics"] == SAMPLE_POINTS

    async def test_end_time_defaults_to_now(self):
        call_ws = AsyncMock(return_value={})
        with patch("app.api.statistics.call_ws", call_ws):
            result = await get_entity_statistics_range("sensor.power", "2026-01-01")
        assert result["end_time"].endswith("Z")
        assert call_ws.await_count == 1

    async def test_flattens_single_entity_result(self):
        with patch("app.api.statistics.call_ws", AsyncMock(return_value={"sensor.x": [1, 2, 3]})):
            result = await get_entity_statistics_range("sensor.x", "2026-01-01")
        assert result["statistics"] == [1, 2, 3]
        assert result["count"] == 3

    async def test_missing_entity_in_result_yields_empty(self):
        with patch("app.api.statistics.call_ws", AsyncMock(return_value={"other": [1]})):
            result = await get_entity_statistics_range("sensor.x", "2026-01-01")
        assert result["statistics"] == []
        assert result["count"] == 0

    @pytest.mark.parametrize("period", sorted(STATISTICS_PERIODS))
    async def test_accepts_every_supported_period(self, period):
        with patch("app.api.statistics.call_ws", AsyncMock(return_value={})):
            result = await get_entity_statistics_range("sensor.x", "2026-01-01", period=period)
        assert result["period"] == period

    async def test_rejects_unknown_period(self):
        result = await get_entity_statistics_range("sensor.x", "2026-01-01", period="fortnight")
        assert "period must be one of" in result["error"]

    async def test_rejects_inverted_range(self):
        result = await get_entity_statistics_range("sensor.x", "2026-01-02", "2026-01-01")
        assert "start_time must be before end_time" in result["error"]


class TestGetLongTermStatistics:
    async def test_looks_back_n_hours(self):
        call_ws = AsyncMock(return_value={"sensor.power": SAMPLE_POINTS})
        with patch("app.api.statistics.call_ws", call_ws):
            result = await get_long_term_statistics("sensor.power", hours=48, period="day")

        start = datetime.strptime(
            call_ws.await_args.kwargs["start_time"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=UTC)
        delta = datetime.now(UTC) - start
        assert timedelta(hours=47) < delta < timedelta(hours=49)
        assert result["period"] == "day"


class TestGetEntityHistoryRange:
    @pytest.fixture
    def captured(self):
        return {}

    @pytest.fixture
    def fake_client(self, captured):
        client = MagicMock()

        async def _get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.json = MagicMock(return_value=[[{"state": "on"}]])
            return response

        client.get = _get
        return client

    async def test_builds_range_request(self, captured, fake_client):
        with patch("app.api.entities.get_client", new=AsyncMock(return_value=fake_client)):
            result = await get_entity_history_range("light.kitchen", "2026-01-15", "2026-01-16")

        assert "/api/history/period/2026-01-15T00:00:00Z" in captured["url"]
        assert captured["params"]["filter_entity_id"] == "light.kitchen"
        assert captured["params"]["end_time"] == "2026-01-16T00:00:00Z"
        assert result == [[{"state": "on"}]]

    async def test_end_time_defaults_to_now(self, captured, fake_client):
        with patch("app.api.entities.get_client", new=AsyncMock(return_value=fake_client)):
            await get_entity_history_range("light.kitchen", "2026-01-15")
        assert captured["params"]["end_time"].endswith("Z")

    async def test_entity_id_is_not_interpolated_into_the_path(self, captured, fake_client):
        """The traversal guard must apply here as it does elsewhere."""
        with patch("app.api.entities.get_client", new=AsyncMock(return_value=fake_client)):
            await get_entity_history_range("../../api/config", "2026-01-15")
        # entity_id travels as a query parameter, never as a path segment
        assert "/api/history/period/2026-01-15T00:00:00Z" in captured["url"]
        assert captured["params"]["filter_entity_id"] == "../../api/config"

    async def test_rejects_inverted_range(self):
        result = await get_entity_history_range("light.kitchen", "2026-01-16", "2026-01-15")
        assert "start_time must be before end_time" in result[0]["error"]
