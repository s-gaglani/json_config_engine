"""
tests/test_config_engine.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Comprehensive pytest-django test suite for the config_engine app.

Covers:
  - ConfigHasher
  - ConfigResolutionService (reads, writes, cache, drift/outdated detection)
  - Cache behaviour (pointer pattern, invalidation)
  - API views via DRF APIClient
  - Constraint enforcement
  - OpenAPI / Swagger UI smoke tests
"""
from __future__ import annotations

import io

import pytest
from django.core.cache import cache
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APIClient

from apps.config_engine.models import ConfigInstance
from apps.config_engine.services import (
    CACHE_TIMEOUT,
    ConfigHasher,
    ConfigResolutionService,
)


# ===========================================================================
# Helpers / shared data
# ===========================================================================

OOB_JSON = {"fields": {"name": {"visible": True}, "email": {"visible": True}}}
TENANT_JSON = {"fields": {"name": {"visible": False}, "email": {"visible": True}}}
USER_JSON = {"fields": {"name": {"visible": True}, "email": {"visible": False}}}

CONFIG_KEY = "invoice.form"
TENANT_ID = "tenant_123"
USER_ID = "user_456"


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def clear_cache():
    """Wipe the cache before every test so pointer keys don't bleed across tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def oob_config(db):
    return ConfigInstance.objects.create(
        config_key=CONFIG_KEY,
        scope_type="oob",
        scope_id=None,
        release_version="v1.0.0",
        config_json=OOB_JSON,
        is_active=True,
    )


@pytest.fixture
def tenant_config(db, oob_config):
    return ConfigResolutionService.create_or_replace_override(
        config_key=CONFIG_KEY,
        scope_type="tenant",
        scope_id=TENANT_ID,
        config_json=TENANT_JSON,
        release_version="v1.0.0",
    )


@pytest.fixture
def user_config(db, oob_config, tenant_config):
    return ConfigResolutionService.create_or_replace_override(
        config_key=CONFIG_KEY,
        scope_type="user",
        scope_id=USER_ID,
        config_json=USER_JSON,
        release_version="v1.0.0",
        parent_config_instance_id=tenant_config.id,
    )


@pytest.fixture
def api_client():
    return APIClient()


# ===========================================================================
# ConfigHasher Tests
# ===========================================================================

class TestConfigHasher:

    def test_hash_is_deterministic(self):
        h1 = ConfigHasher.generate_hash(OOB_JSON)
        h2 = ConfigHasher.generate_hash(OOB_JSON)
        assert h1 == h2

    def test_hash_differs_for_different_json(self):
        h1 = ConfigHasher.generate_hash({"a": 1})
        h2 = ConfigHasher.generate_hash({"a": 2})
        assert h1 != h2

    def test_hash_is_order_independent(self):
        """Key insertion order must not affect the hash."""
        h1 = ConfigHasher.generate_hash({"a": 1, "b": 2})
        h2 = ConfigHasher.generate_hash({"b": 2, "a": 1})
        assert h1 == h2

    def test_hash_is_64_hex_chars(self):
        h = ConfigHasher.generate_hash({"x": "y"})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ===========================================================================
# ConfigResolutionService Tests
# ===========================================================================

@pytest.mark.django_db
class TestConfigResolutionService:

    def test_resolution_returns_oob_when_no_overrides(self, oob_config):
        result = ConfigResolutionService.get_effective_config(CONFIG_KEY)
        assert result["source"] == "oob"
        assert result["config"] == OOB_JSON
        assert result["release"] == "v1.0.0"

    def test_resolution_returns_tenant_over_oob(self, tenant_config):
        result = ConfigResolutionService.get_effective_config(
            CONFIG_KEY, tenant_id=TENANT_ID
        )
        assert result["source"] == "tenant"
        assert result["config"] == TENANT_JSON

    def test_resolution_returns_user_over_tenant(self, user_config):
        result = ConfigResolutionService.get_effective_config(
            CONFIG_KEY, tenant_id=TENANT_ID, user_id=USER_ID
        )
        assert result["source"] == "user"
        assert result["config"] == USER_JSON

    def test_resolution_raises_if_no_oob(self, db):
        with pytest.raises(ConfigInstance.DoesNotExist):
            ConfigResolutionService.get_effective_config("nonexistent.key")

    def test_create_override_deactivates_previous(self, tenant_config):
        """After creating a second override the first must be is_active=False."""
        first_id = tenant_config.id

        second = ConfigResolutionService.create_or_replace_override(
            config_key=CONFIG_KEY,
            scope_type="tenant",
            scope_id=TENANT_ID,
            config_json={"updated": True},
            release_version="v1.1.0",
        )

        first = ConfigInstance.objects.get(pk=first_id)
        assert first.is_active is False
        assert second.is_active is True

    def test_create_override_stores_lineage(self, oob_config):
        """New override must carry base_config_id, base_release_version, base_config_hash."""
        override = ConfigResolutionService.create_or_replace_override(
            config_key=CONFIG_KEY,
            scope_type="tenant",
            scope_id=TENANT_ID,
            config_json=TENANT_JSON,
            release_version="v1.0.0",
        )

        assert override.base_config_id == oob_config.id
        assert override.base_release_version == oob_config.release_version
        expected_hash = ConfigHasher.generate_hash(oob_config.config_json)
        assert override.base_config_hash == expected_hash

    def test_reset_to_oob_deactivates_override(self, tenant_config):
        ConfigResolutionService.reset_to_oob(
            config_key=CONFIG_KEY,
            scope_type="tenant",
            scope_id=TENANT_ID,
        )
        tenant_config.refresh_from_db()
        assert tenant_config.is_active is False

    def test_detect_outdated_tenant_configs(self, tenant_config):
        """After a new OOB v2.0.0 is activated, the v1-based tenant should appear as outdated."""
        # Deactivate old OOB, create new one
        ConfigInstance.objects.filter(scope_type="oob", config_key=CONFIG_KEY).update(is_active=False)
        ConfigInstance.objects.create(
            config_key=CONFIG_KEY,
            scope_type="oob",
            scope_id=None,
            release_version="v2.0.0",
            config_json={"fields": {"name": {"visible": True}, "phone": {"visible": True}}},
            is_active=True,
        )

        outdated_qs = ConfigResolutionService.detect_outdated_tenant_configs()
        outdated_ids = list(outdated_qs.values_list("id", flat=True))
        assert tenant_config.id in outdated_ids

    def test_detect_drift_true_when_oob_changed(self, tenant_config, oob_config):
        """Drift is detected when OOB payload changes but tenant base_config_hash is stale."""
        # Deactivate old OOB, create new one with different JSON
        oob_config.is_active = False
        oob_config.save()
        ConfigInstance.objects.create(
            config_key=CONFIG_KEY,
            scope_type="oob",
            release_version="v2.0.0",
            config_json={"fields": {"CHANGED": True}},
            is_active=True,
        )

        assert ConfigResolutionService.detect_drift(tenant_config) is True

    def test_detect_drift_false_when_in_sync(self, tenant_config):
        """No drift when tenant base_config_hash matches the current OOB hash."""
        assert ConfigResolutionService.detect_drift(tenant_config) is False


# ===========================================================================
# Cache Tests
# ===========================================================================

@pytest.mark.django_db
class TestCacheBehaviour:

    def test_cache_key_includes_release(self, oob_config):
        """After resolution, the pointer key must exist and hold the release version."""
        ConfigResolutionService.get_effective_config(CONFIG_KEY)

        ptr_key = ConfigResolutionService._ptr_key(CONFIG_KEY, None, None)
        cached_release = cache.get(ptr_key)
        assert cached_release == "v1.0.0"

    def test_cache_hit_returns_same_result(self, oob_config):
        """Second call must return identical result (served from cache)."""
        first = ConfigResolutionService.get_effective_config(CONFIG_KEY)
        second = ConfigResolutionService.get_effective_config(CONFIG_KEY)
        assert first == second

    def test_cache_is_invalidated_on_override(self, oob_config):
        """After creating an override, the pointer key must be gone."""
        ConfigResolutionService.get_effective_config(CONFIG_KEY, tenant_id=TENANT_ID)

        ptr_key = ConfigResolutionService._ptr_key(CONFIG_KEY, TENANT_ID, None)
        assert cache.get(ptr_key) == "v1.0.0"  # populated

        ConfigResolutionService.create_or_replace_override(
            config_key=CONFIG_KEY,
            scope_type="tenant",
            scope_id=TENANT_ID,
            config_json=TENANT_JSON,
            release_version="v1.0.0",
        )

        assert cache.get(ptr_key) is None  # invalidated

    def test_cache_is_invalidated_on_reset(self, tenant_config):
        """reset_to_oob must clear the pointer key for the affected scope."""
        ConfigResolutionService.get_effective_config(CONFIG_KEY, tenant_id=TENANT_ID)

        ptr_key = ConfigResolutionService._ptr_key(CONFIG_KEY, TENANT_ID, None)
        assert cache.get(ptr_key) is not None

        ConfigResolutionService.reset_to_oob(CONFIG_KEY, "tenant", TENANT_ID)
        assert cache.get(ptr_key) is None

    def test_cache_is_invalidated_on_oob_load(self, oob_config, tmp_path):
        """
        load_oob_config management command must call invalidate_cache so the OOB
        pointer key is cleared before the new OOB is served.
        """
        # Warm the cache with the current OOB result
        ConfigResolutionService.get_effective_config(CONFIG_KEY)
        ptr_key = ConfigResolutionService._ptr_key(CONFIG_KEY, None, None)
        assert cache.get(ptr_key) is not None

        # Write a new OOB JSON fixture to a temp file
        import json
        new_payload = {"fields": {"name": {"visible": True}, "phone": {"visible": True}}}
        config_file = tmp_path / "new_oob.json"
        config_file.write_text(json.dumps(new_payload))

        # Run the management command (new version so it won't be rejected as duplicate)
        out = io.StringIO()
        call_command(
            "load_oob_config",
            "--config-key", CONFIG_KEY,
            "--release-version", "v2.0.0",
            "--config-file", str(config_file),
            stdout=out,
        )

        # The pointer key must be cleared
        assert cache.get(ptr_key) is None


# ===========================================================================
# API View Tests
# ===========================================================================

@pytest.mark.django_db
class TestAPIViews:

    # ── GET /api/v1/config/ ──────────────────────────────────────────────────

    def test_get_effective_config_oob(self, api_client, oob_config):
        r = api_client.get("/api/v1/config/", {"key": CONFIG_KEY})
        assert r.status_code == status.HTTP_200_OK
        assert r.data["source"] == "oob"
        assert r.data["config"] == OOB_JSON

    def test_get_effective_config_tenant_override(self, api_client, tenant_config):
        r = api_client.get("/api/v1/config/", {"key": CONFIG_KEY, "tenant_id": TENANT_ID})
        assert r.status_code == status.HTTP_200_OK
        assert r.data["source"] == "tenant"
        assert r.data["config"] == TENANT_JSON

    def test_get_effective_config_user_override(self, api_client, user_config):
        r = api_client.get(
            "/api/v1/config/",
            {"key": CONFIG_KEY, "tenant_id": TENANT_ID, "user_id": USER_ID},
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.data["source"] == "user"
        assert r.data["config"] == USER_JSON

    def test_get_effective_config_404_if_missing(self, api_client, db):
        r = api_client.get("/api/v1/config/", {"key": "does.not.exist"})
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_get_effective_config_400_if_key_missing(self, api_client, db):
        r = api_client.get("/api/v1/config/")
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    # ── POST /api/v1/config/override/ ───────────────────────────────────────

    def test_create_override_success(self, api_client, oob_config):
        expected_hash = ConfigHasher.generate_hash(OOB_JSON)
        payload = {
            "config_key": CONFIG_KEY,
            "scope_type": "tenant",
            "scope_id": TENANT_ID,
            "config_json": TENANT_JSON,
            "release_version": "v1.0.0",
            "base_config_id": str(oob_config.id),
            "base_release_version": oob_config.release_version,
            "base_config_hash": expected_hash,
        }
        r = api_client.post("/api/v1/config/override/", payload, format="json")
        assert r.status_code == status.HTTP_201_CREATED
        body = r.data

        # Lineage fields must be populated automatically
        assert body["base_config_id"] == str(oob_config.id)
        assert body["base_release_version"] == oob_config.release_version
        assert body["base_config_hash"] == ConfigHasher.generate_hash(OOB_JSON)
        assert body["is_active"] is True

    def test_create_override_rejects_oob_scope(self, api_client, db):
        payload = {
            "config_key": CONFIG_KEY,
            "scope_type": "oob",
            "config_json": OOB_JSON,
            "release_version": "v1.0.0",
        }
        r = api_client.post("/api/v1/config/override/", payload, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "immutable" in r.data["scope_type"][0].lower()

    def test_reset_to_oob_blocks_oob_scope(self, api_client, oob_config):
        """POST /api/v1/config/reset/ with scope_type='oob' must return 400."""
        payload = {
            "config_key": CONFIG_KEY,
            "scope_type": "oob",
            "scope_id": "any",
        }
        r = api_client.post("/api/v1/config/reset/", payload, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "OOB configs cannot be reset" in r.data["error"]

    def test_create_override_400_missing_fields(self, api_client, db):
        r = api_client.post("/api/v1/config/override/", {"config_key": CONFIG_KEY}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    # ── POST /api/v1/config/reset/ ──────────────────────────────────────────

    def test_reset_to_oob(self, api_client, tenant_config):
        payload = {
            "config_key": CONFIG_KEY,
            "scope_type": "tenant",
            "scope_id": TENANT_ID,
        }
        r = api_client.post("/api/v1/config/reset/", payload, format="json")
        assert r.status_code == status.HTTP_204_NO_CONTENT

        tenant_config.refresh_from_db()
        assert tenant_config.is_active is False

    # ── GET /api/v1/config/lineage/ ─────────────────────────────────────────

    def test_get_lineage(self, api_client, tenant_config):
        # Create a second (replacement) override so first becomes inactive
        ConfigResolutionService.create_or_replace_override(
            config_key=CONFIG_KEY,
            scope_type="tenant",
            scope_id=TENANT_ID,
            config_json={"updated": True},
            release_version="v1.1.0",
        )

        r = api_client.get("/api/v1/config/lineage/", {"config_key": CONFIG_KEY})
        assert r.status_code == status.HTTP_200_OK
        # All records (oob + first tenant + second tenant) are returned
        assert len(r.data) >= 2
        # Inactive records are also included
        is_active_values = {item["is_active"] for item in r.data}
        assert False in is_active_values

    # ── GET /api/v1/config/diff/ ────────────────────────────────────────────

    def test_diff_view_detects_drift(self, api_client, tenant_config, oob_config):
        """When OOB payload changes, diff endpoint reports is_drifted=True."""
        oob_config.is_active = False
        oob_config.save()
        ConfigInstance.objects.create(
            config_key=CONFIG_KEY,
            scope_type="oob",
            release_version="v2.0.0",
            config_json={"fields": {"CHANGED": True}},
            is_active=True,
        )

        r = api_client.get(
            "/api/v1/config/diff/",
            {"config_key": CONFIG_KEY, "scope_type": "tenant", "scope_id": TENANT_ID},
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.data["is_drifted"] is True

    def test_diff_view_detects_outdated(self, api_client, tenant_config):
        """After a new OOB version, diff endpoint reports is_outdated=True."""
        ConfigInstance.objects.filter(scope_type="oob", config_key=CONFIG_KEY).update(is_active=False)
        ConfigInstance.objects.create(
            config_key=CONFIG_KEY,
            scope_type="oob",
            scope_id=None,
            release_version="v2.0.0",
            config_json={"updated": True},
            is_active=True,
        )

        r = api_client.get(
            "/api/v1/config/diff/",
            {"config_key": CONFIG_KEY, "scope_type": "tenant", "scope_id": TENANT_ID},
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.data["is_outdated"] is True

    def test_diff_view_in_sync(self, api_client, tenant_config):
        r = api_client.get(
            "/api/v1/config/diff/",
            {"config_key": CONFIG_KEY, "scope_type": "tenant", "scope_id": TENANT_ID},
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.data["is_drifted"] is False
        assert r.data["is_outdated"] is False

    def test_diff_view_404_for_missing_scope(self, api_client, oob_config):
        r = api_client.get(
            "/api/v1/config/diff/",
            {"config_key": CONFIG_KEY, "scope_type": "tenant", "scope_id": "nonexistent"},
        )
        assert r.status_code == status.HTTP_404_NOT_FOUND

    # ── GET /api/v1/config/outdated/ ────────────────────────────────────────

    def test_outdated_configs_view(self, api_client, tenant_config):
        ConfigInstance.objects.filter(scope_type="oob", config_key=CONFIG_KEY).update(is_active=False)
        ConfigInstance.objects.create(
            config_key=CONFIG_KEY,
            scope_type="oob",
            scope_id=None,
            release_version="v2.0.0",
            config_json={"updated": True},
            is_active=True,
        )

        r = api_client.get("/api/v1/config/outdated/")
        assert r.status_code == status.HTTP_200_OK
        outdated_ids = [item["id"] for item in r.data]
        assert str(tenant_config.id) in outdated_ids

    def test_outdated_configs_view_empty_when_all_in_sync(self, api_client, tenant_config):
        r = api_client.get("/api/v1/config/outdated/")
        assert r.status_code == status.HTTP_200_OK
        assert r.data == []


# ===========================================================================
# Constraint Tests
# ===========================================================================

@pytest.mark.django_db
class TestConstraints:

    def test_oob_config_is_immutable_via_api(self, api_client, db):
        """POST /api/v1/config/override/ with scope_type='oob' must return 400."""
        payload = {
            "config_key": CONFIG_KEY,
            "scope_type": "oob",
            "config_json": OOB_JSON,
            "release_version": "v1.0.0",
        }
        r = api_client.post("/api/v1/config/override/", payload, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_only_one_active_config_per_scope(self, tenant_config):
        """create_or_replace_override must ensure at most one active config per scope."""
        ConfigResolutionService.create_or_replace_override(
            config_key=CONFIG_KEY,
            scope_type="tenant",
            scope_id=TENANT_ID,
            config_json={"v2": True},
            release_version="v2.0.0",
        )

        active_count = ConfigInstance.objects.filter(
            config_key=CONFIG_KEY,
            scope_type="tenant",
            scope_id=TENANT_ID,
            is_active=True,
        ).count()
        assert active_count == 1

    def test_no_partial_update_allowed(self, api_client, db):
        """PATCH is not a registered method on the override endpoint — must return 405."""
        r = api_client.patch("/api/v1/config/override/", {}, format="json")
        assert r.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_reset_endpoint_400_when_fields_missing(self, api_client, db):
        r = api_client.post("/api/v1/config/reset/", {"config_key": CONFIG_KEY}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_lineage_400_when_config_key_missing(self, api_client, db):
        r = api_client.get("/api/v1/config/lineage/")
        assert r.status_code == status.HTTP_400_BAD_REQUEST


# ===========================================================================
# Swagger / OpenAPI Schema Smoke Tests
# ===========================================================================

@pytest.mark.django_db
class TestSwaggerSchema:

    def test_openapi_schema_loads(self, api_client):
        r = api_client.get("/api/schema/")
        assert r.status_code == status.HTTP_200_OK
        content_type = r.headers.get("Content-Type", "")
        assert (
            "application/vnd.oai.openapi" in content_type
            or "application/json" in content_type
            or "application/yaml" in content_type
        ), f"Unexpected Content-Type: {content_type}"

    def test_swagger_ui_loads(self, api_client):
        r = api_client.get("/api/docs/")
        assert r.status_code == status.HTTP_200_OK

    def test_redoc_ui_loads(self, api_client):
        r = api_client.get("/api/redoc/")
        assert r.status_code == status.HTTP_200_OK

    def test_schema_contains_all_endpoints(self, api_client):
        """Every registered API path must appear in the generated schema."""
        r = api_client.get("/api/schema/?format=json")
        assert r.status_code == status.HTTP_200_OK
        schema = r.json()
        paths = schema.get("paths", {})

        expected_paths = [
            "/api/v1/config/",
            "/api/v1/config/override/",
            "/api/v1/config/reset/",
            "/api/v1/config/lineage/",
            "/api/v1/config/diff/",
            "/api/v1/config/outdated/",
        ]
        for ep in expected_paths:
            assert ep in paths, f"Missing endpoint in schema: {ep}"
