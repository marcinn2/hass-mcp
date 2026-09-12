"""Tests for the access policy layer.

Ported alongside the feature from paultanger/ha-mcp-server, with the posture
inverted: this project defaults to permissive, so the first thing these tests
pin down is that an unconfigured server behaves exactly as before.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import config
from app.api.entities import get_entities, get_entity_state
from app.api.services import call_service
from app.core import policy
from app.core.cache.manager import get_cache_manager

ENTITIES = [
    {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
    {"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
    {"entity_id": "sensor.temperature", "state": "21", "attributes": {}},
]


@pytest.fixture(autouse=True)
def _token():
    with patch("app.core.decorators.HA_TOKEN", "test-token"):
        yield


@pytest.fixture(autouse=True)
async def _cold_cache():
    """Reads are filtered inside cached functions, so each test needs a cold cache."""
    cache = await get_cache_manager()
    await cache.clear()
    yield
    await cache.clear()


@pytest.fixture
def fake_client():
    client = MagicMock()

    async def _get(url, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=list(ENTITIES))
        return response

    async def _post(url, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=[])
        return response

    client.get = _get
    client.post = _post
    return client


def patched_clients(fake_client):
    return (
        patch("app.api.entities.get_client", new=AsyncMock(return_value=fake_client)),
        patch("app.api.services.get_client", new=AsyncMock(return_value=fake_client)),
    )


class TestPermissiveByDefault:
    """An unconfigured server must behave exactly as it did before the feature."""

    def test_read_only_is_off(self):
        assert config.POLICY_READ_ONLY is False

    def test_lists_are_empty(self):
        assert config.POLICY_ENTITY_ALLOWLIST == []
        assert config.POLICY_CONTROL_DENYLIST == []

    def test_empty_allowlist_allows_everything(self):
        """This is the deliberate inversion: the source fork fails closed here."""
        assert policy.is_allowed("light.kitchen") is True
        assert policy.is_allowed("anything.at.all") is True

    def test_empty_denylist_denies_nothing(self):
        assert policy.control_denied("lock.front_door") is False

    def test_filter_is_a_no_op(self):
        assert policy.filter_entities(ENTITIES) is ENTITIES

    def test_describe(self):
        assert policy.describe() == {
            "read_only": False,
            "allowlist_patterns": 0,
            "denylist_patterns": 0,
        }


class TestIsAllowed:
    def test_exact_match(self):
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.kitchen"]):
            assert policy.is_allowed("light.kitchen") is True
            assert policy.is_allowed("light.bedroom") is False

    def test_glob_match(self):
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["sensor.gpu_*"]):
            assert policy.is_allowed("sensor.gpu_temp") is True
            assert policy.is_allowed("sensor.cpu_temp") is False

    def test_matching_is_case_sensitive(self):
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.kitchen"]):
            assert policy.is_allowed("LIGHT.KITCHEN") is False

    def test_empty_entity_id_is_denied_when_a_list_exists(self):
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            assert policy.is_allowed("") is False


class TestControlDenied:
    def test_read_only_denies_all_control(self):
        with patch.object(config, "POLICY_READ_ONLY", True):
            assert policy.control_denied("light.kitchen") is True

    def test_denylist_glob(self):
        with patch.object(config, "POLICY_CONTROL_DENYLIST", ["lock.*"]):
            assert policy.control_denied("lock.front_door") is True
            assert policy.control_denied("light.kitchen") is False

    def test_allowlist_failure_also_denies_control(self):
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            assert policy.control_denied("lock.front_door") is True

    def test_denylist_beats_allowlist(self):
        """A permanent safety floor: allowed to read, never to control."""
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["lock.*"]):
            with patch.object(config, "POLICY_CONTROL_DENYLIST", ["lock.front_door"]):
                assert policy.is_allowed("lock.front_door") is True
                assert policy.control_denied("lock.front_door") is True


class TestDeniedReason:
    def test_read_only_reason_names_the_setting(self):
        with patch.object(config, "POLICY_READ_ONLY", True):
            reason = policy.denied_reason("light.kitchen", control=True)
        assert "read-only" in reason
        assert "policy.read_only" in reason

    def test_allowlist_reason_names_the_entity(self):
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            reason = policy.denied_reason("lock.front_door")
        assert "lock.front_door" in reason
        assert "allowlist" in reason

    def test_denylist_reason_explains_read_versus_control(self):
        with patch.object(config, "POLICY_CONTROL_DENYLIST", ["lock.*"]):
            reason = policy.denied_reason("lock.front_door", control=True)
        assert "denylist" in reason
        assert "read but never controlled" in reason

    def test_denied_returns_the_error_envelope(self):
        with patch.object(config, "POLICY_READ_ONLY", True):
            result = policy.denied("light.kitchen", control=True)
        assert "error" in result


class TestFilterEntities:
    def test_filters_to_the_allowlist(self):
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*", "sensor.*"]):
            result = policy.filter_entities(ENTITIES)
        assert [e["entity_id"] for e in result] == ["light.kitchen", "sensor.temperature"]

    def test_error_envelope_dict_passes_through(self):
        envelope = {"error": "Connection error"}
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            assert policy.filter_entities(envelope) is envelope

    def test_error_envelope_list_passes_through(self):
        envelope = [{"error": "Connection error"}]
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            assert policy.filter_entities(envelope) is envelope

    def test_non_list_passes_through(self):
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            assert policy.filter_entities("not a list") == "not a list"


class TestEnforcementAtChokePoints:
    async def test_default_allows_control(self, fake_client):
        entities_patch, services_patch = patched_clients(fake_client)
        with entities_patch, services_patch:
            result = await call_service("light", "turn_on", {"entity_id": "light.kitchen"})
        assert "error" not in result

    async def test_read_only_blocks_service_calls(self, fake_client):
        entities_patch, services_patch = patched_clients(fake_client)
        with patch.object(config, "POLICY_READ_ONLY", True):
            with entities_patch, services_patch:
                result = await call_service("light", "turn_on", {"entity_id": "light.kitchen"})
        assert "read-only" in result["error"]

    async def test_read_only_blocks_untargeted_service_calls(self, fake_client):
        """A service call with no entity_id still changes state."""
        entities_patch, services_patch = patched_clients(fake_client)
        with patch.object(config, "POLICY_READ_ONLY", True):
            with entities_patch, services_patch:
                result = await call_service("homeassistant", "restart")
        assert "read-only" in result["error"]

    async def test_read_only_still_allows_reads(self, fake_client):
        entities_patch, services_patch = patched_clients(fake_client)
        with patch.object(config, "POLICY_READ_ONLY", True):
            with entities_patch, services_patch:
                result = await get_entities()
        assert len(result) == 3

    async def test_allowlist_filters_entity_lists(self, fake_client):
        entities_patch, services_patch = patched_clients(fake_client)
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            with entities_patch, services_patch:
                result = await get_entities()
        assert [e["entity_id"] for e in result] == ["light.kitchen"]

    async def test_allowlist_blocks_single_entity_reads(self, fake_client):
        entities_patch, services_patch = patched_clients(fake_client)
        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            with entities_patch, services_patch:
                result = await get_entity_state("lock.front_door")
        assert "allowlist" in result["error"]

    async def test_denylist_blocks_control_but_not_reads(self, fake_client):
        entities_patch, services_patch = patched_clients(fake_client)
        with patch.object(config, "POLICY_CONTROL_DENYLIST", ["lock.*"]):
            with entities_patch, services_patch:
                denied = await call_service("lock", "unlock", {"entity_id": "lock.front_door"})
                read = await get_entity_state("lock.front_door")
        assert "denylist" in denied["error"]
        assert "error" not in read

    async def test_a_list_of_targets_is_checked_entry_by_entry(self, fake_client):
        entities_patch, services_patch = patched_clients(fake_client)
        with patch.object(config, "POLICY_CONTROL_DENYLIST", ["lock.*"]):
            with entities_patch, services_patch:
                result = await call_service(
                    "homeassistant",
                    "turn_off",
                    {"entity_id": ["light.kitchen", "lock.front_door"]},
                )
        assert "denylist" in result["error"]


class TestManageItemWriteGate:
    """Config CRUD posts straight to /api/config/..., bypassing call_service."""

    @pytest.mark.parametrize(
        "action",
        ["create", "update", "delete", "enable", "disable", "trigger", "activate", "reload"],
    )
    async def test_write_actions_blocked_in_read_only(self, action):
        from app.tools.unified import manage_item

        with patch.object(config, "POLICY_READ_ONLY", True):
            result = await manage_item(
                action=action, item_type="automation", item_id="x", config={"alias": "a"}
            )
        assert "read-only" in result["error"]

    async def test_write_actions_set_covers_every_dispatched_action(self):
        """A new write action added without updating the set would bypass the gate."""
        import re
        from pathlib import Path

        from app.tools.unified import WRITE_ACTIONS

        source = Path("app/tools/unified.py").read_text()
        start = source.index("async def manage_item")
        end = source.index("async def ", start + 10)
        dispatched = set(re.findall(r'if action == "([a-z_]+)"', source[start:end]))

        assert dispatched <= WRITE_ACTIONS

    async def test_writes_allowed_by_default(self):
        from app.tools.unified import manage_item

        create = AsyncMock(return_value={"id": "new"})
        with patch("app.tools.unified.automations.create_automation", create):
            result = await manage_item(
                action="create", item_type="automation", config={"alias": "a"}
            )
        assert result == {"id": "new"}


class TestReadOnlyAtTheClient:
    """read_only is enforced on the shared HTTP client.

    Several API functions build service URLs themselves instead of calling
    call_service (run_script even does so deliberately), so a choke point in
    call_service alone left them unguarded. Enforcing on the client covers
    every call site, including ones added later.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "/api/services/homeassistant/restart",
            "/api/services/script/morning",
            "/api/config/automation/config/123",
            "/api/webhook/abc",
        ],
    )
    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_writes_refused_in_read_only(self, method, path):
        with patch.object(config, "POLICY_READ_ONLY", True):
            assert policy.request_denied(method, path) is not None

    @pytest.mark.parametrize("method", ["POST", "DELETE"])
    def test_writes_allowed_by_default(self, method):
        assert policy.request_denied(method, "/api/services/light/turn_on") is None

    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    def test_reads_always_allowed(self, method):
        with patch.object(config, "POLICY_READ_ONLY", True):
            assert policy.request_denied(method, "/api/states") is None

    @pytest.mark.parametrize("path", ["/api/template", "/api/config/core/check_config"])
    def test_post_shaped_reads_stay_allowed(self, path):
        """Rendering a template and validating config change nothing."""
        with patch.object(config, "POLICY_READ_ONLY", True):
            assert policy.request_denied("POST", path) is None

    async def test_hook_raises_policy_violation(self):
        import httpx

        from app.core.client import _enforce_policy

        request = httpx.Request("POST", "http://ha.local:8123/api/services/script/x")
        with patch.object(config, "POLICY_READ_ONLY", True):
            with pytest.raises(policy.PolicyViolation, match="read-only"):
                await _enforce_policy(request)

    async def test_hook_passes_reads_through(self):
        import httpx

        from app.core.client import _enforce_policy

        request = httpx.Request("GET", "http://ha.local:8123/api/states")
        with patch.object(config, "POLICY_READ_ONLY", True):
            assert await _enforce_policy(request) is None

    # Note: conftest autouse-mocks httpx.AsyncClient and get_client for every
    # test, so the real client is never constructed here. That the hook is wired
    # in is asserted in tests/unit/test_core_client.py, which patches the
    # constructor and inspects its kwargs.

    async def test_violation_becomes_a_clean_error_not_unexpected(self):
        """handle_api_errors must name the policy, not report a crash.

        Without explicit handling a PolicyViolation would fall through to the
        generic handler and surface as "Unexpected error: ...", which reads like
        a bug in the server rather than a deliberate refusal.
        """
        from app.core.decorators import handle_api_errors

        @handle_api_errors
        async def writes_something() -> dict:
            raise policy.PolicyViolation(
                "Refused: the server is in read-only mode, so POST /api/x is not allowed."
            )

        with patch("app.core.decorators.HA_TOKEN", "test-token"):
            result = await writes_something()

        assert "read-only" in result["error"]
        assert "Unexpected error" not in result["error"]

    async def test_violation_error_shape_follows_the_return_annotation(self):
        """List-returning functions must still get a list-shaped error."""
        from app.core.decorators import handle_api_errors

        @handle_api_errors
        async def lists_something() -> list[dict]:
            raise policy.PolicyViolation("Refused: read-only mode.")

        with patch("app.core.decorators.HA_TOKEN", "test-token"):
            result = await lists_something()

        assert isinstance(result, list)
        assert "read-only" in result[0]["error"]


class TestQualify:
    @pytest.mark.parametrize(
        ("domain", "raw", "expected"),
        [
            ("automation", "morning", "automation.morning"),
            ("automation", "automation.morning", "automation.morning"),
            ("script", "notify", "script.notify"),
            ("script", "script.notify", "script.notify"),
        ],
    )
    def test_qualification(self, domain, raw, expected):
        assert policy.qualify(domain, raw) == expected


class TestEntityScopedGuards:
    """The allowlist must cover history, logbook, statistics and traces too.

    Before this, an allowlist blocked current state but left the same entity's
    history and logbook readable.
    """

    async def test_history_is_guarded(self):
        from app.api.entities import get_entity_history

        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            result = await get_entity_history("lock.front_door", 24)

        assert "allowlist" in result[0]["error"]

    async def test_history_range_is_guarded(self):
        from app.api.entities import get_entity_history_range

        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            result = await get_entity_history_range("lock.front_door", "2026-01-01")

        assert "allowlist" in result[0]["error"]

    async def test_logbook_is_guarded(self):
        from app.api.logbook import get_entity_logbook

        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            result = await get_entity_logbook("lock.front_door", 24)

        assert "allowlist" in result[0]["error"]

    async def test_long_term_statistics_are_guarded(self):
        from app.api.statistics import get_entity_statistics_range

        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["light.*"]):
            result = await get_entity_statistics_range("sensor.power", "2026-01-01")

        assert "allowlist" in result["error"]

    async def test_traces_are_guarded(self):
        from app.api.traces import get_automation_traces_data

        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["automation.allowed"]):
            result = await get_automation_traces_data("automation.secret")

        assert "allowlist" in result["error"]

    async def test_execution_log_is_guarded_via_qualification(self):
        """automation_id arrives without its domain prefix."""
        from app.api.automations import get_automation_execution_log

        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["automation.allowed"]):
            result = await get_automation_execution_log("secret")

        assert "automation.secret" in result["error"]

    async def test_script_config_is_guarded_via_qualification(self):
        from app.api.scripts import get_script_config

        with patch.object(config, "POLICY_ENTITY_ALLOWLIST", ["script.allowed"]):
            result = await get_script_config("secret")

        assert "script.secret" in result["error"]

    @pytest.mark.parametrize(
        ("module", "func", "raw_id", "domain"),
        [
            ("app.api.automations", "trigger_automation", "secret", "automation"),
            ("app.api.automations", "enable_automation", "secret", "automation"),
            ("app.api.automations", "disable_automation", "secret", "automation"),
            ("app.api.scripts", "run_script", "secret", "script"),
        ],
    )
    async def test_control_actions_respect_the_denylist(self, module, func, raw_id, domain):
        import importlib

        target = getattr(importlib.import_module(module), func)
        with patch.object(config, "POLICY_CONTROL_DENYLIST", [f"{domain}.*"]):
            result = await target(raw_id)

        assert "denylist" in result["error"]
