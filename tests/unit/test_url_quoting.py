"""Tests for URL path segment quoting.

Identifiers supplied by MCP callers are interpolated into Home Assistant REST
paths. Without encoding, a value such as "../../api/config" collapses during URL
normalization and redirects the request — with the admin token attached — to an
unintended endpoint. These tests pin that behaviour shut.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.urls import quote_path, quote_segment


class TestQuoteSegment:
    """Tests for quote_segment."""

    @pytest.mark.parametrize(
        "value",
        [
            "light.living_room",
            "automation.morning_routine",
            "sensor.outdoor_temperature_2",
            "my-tag_id.v1~test",
        ],
    )
    def test_ordinary_identifiers_pass_through(self, value):
        """Legitimate Home Assistant identifiers must not be altered."""
        assert quote_segment(value) == value

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("../../api/config", "..%2F..%2Fapi%2Fconfig"),
            ("../error_log", "..%2Ferror_log"),
            ("light.k?x=1", "light.k%3Fx%3D1"),
            ("light.k#frag", "light.k%23frag"),
            ("a/b", "a%2Fb"),
        ],
    )
    def test_separators_and_delimiters_are_encoded(self, value, expected):
        """Path, query and fragment delimiters must be percent-encoded."""
        assert quote_segment(value) == expected

    def test_non_string_input_is_coerced(self):
        assert quote_segment(123) == "123"


class TestQuotePath:
    """Tests for quote_path, used for genuinely hierarchical identifiers."""

    def test_preserves_separators(self):
        assert (
            quote_path("automation/homeassistant/motion_light.yaml")
            == "automation/homeassistant/motion_light.yaml"
        )

    def test_strips_traversal_segments(self):
        assert quote_path("../../api/config") == "api/config"
        assert quote_path("a/./b/../c") == "a/b/c"

    def test_encodes_within_segments(self):
        assert quote_path("a/b?c") == "a/b%3Fc"


class TestRequestURLsAreConfined:
    """End-to-end: a hostile identifier cannot leave its intended endpoint."""

    @pytest.fixture
    def captured(self):
        return []

    @pytest.fixture
    def fake_client(self, captured):
        client = MagicMock()

        async def _record(url, **kwargs):
            captured.append(url)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.json = MagicMock(
                return_value={"entity_id": "x", "state": "on", "attributes": {}}
            )
            return response

        client.get = _record
        client.post = _record
        return client

    @pytest.mark.parametrize(
        "entity_id",
        ["../../api/config", "../error_log", "light.k?x=1", "light.k#frag"],
    )
    async def test_get_entity_state_stays_under_states(self, entity_id, captured, fake_client):
        from app.api.entities import get_entity_state

        with patch("app.core.decorators.HA_TOKEN", "test-token"):
            with patch("app.api.entities.get_client", new=AsyncMock(return_value=fake_client)):
                await get_entity_state(entity_id)

        assert len(captured) == 1
        assert "/api/states/" in captured[0]
        assert captured[0].split("/api/states/")[1].count("/") == 0

    async def test_valid_entity_id_is_unchanged(self, captured, fake_client):
        from app.api.entities import get_entity_state

        with patch("app.core.decorators.HA_TOKEN", "test-token"):
            with patch("app.api.entities.get_client", new=AsyncMock(return_value=fake_client)):
                await get_entity_state("light.living_room")

        assert captured[0].endswith("/api/states/light.living_room")

    async def test_call_service_domain_is_confined(self, captured, fake_client):
        from app.api.services import call_service

        with patch("app.core.decorators.HA_TOKEN", "test-token"):
            with patch("app.api.services.get_client", new=AsyncMock(return_value=fake_client)):
                await call_service("../../api", "config")

        assert captured[0].endswith("/api/services/..%2F..%2Fapi/config")
