import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from rest_framework import status
from rest_framework.test import APIClient

from apps.config_engine.models import ConfigInstance

@pytest.mark.django_db
class TestOOBConstraints:

    def setup_method(self):
        self.api_client = APIClient()

    def test_duplicate_oob_blocked_at_model_level(self):
        """Only one OOB per (config_key, release_version)."""
        ConfigInstance.objects.create(
            config_key="test.key",
            scope_type="oob",
            release_version="v1",
            config_json={"foo": "bar"}
        )
        
        with pytest.raises(ValidationError):
            ConfigInstance.objects.create(
                config_key="test.key",
                scope_type="oob",
                release_version="v1",
                config_json={"fixed": True}
            )

    def test_oob_immutability(self):
        """Updating an OOB instance's core fields should fail."""
        oob = ConfigInstance.objects.create(
            config_key="oob.key",
            scope_type="oob",
            release_version="v1",
            config_json={"initial": True}
        )
        
        oob.config_json = {"initial": False}
        with pytest.raises(ValidationError) as exc:
            oob.save()
        assert "ConfigInstance is immutable. Field 'config_json' cannot be changed after creation." in str(exc.value)

    def test_tenant_immutability(self):
        """Updating a tenant instance's core fields should fail."""
        tenant = ConfigInstance.objects.create(
            config_key="tenant.key",
            scope_type="tenant",
            scope_id="t1",
            release_version="v1",
            config_json={"initial": True},
            base_config_id="00000000-0000-0000-0000-000000000001",
            base_release_version="v1",
            base_config_hash="dummy_hash",
        )
        
        tenant.config_json = {"initial": False}
        with pytest.raises(ValidationError) as exc:
            tenant.save()
        assert "ConfigInstance is immutable. Field 'config_json' cannot be changed after creation." in str(exc.value)

    def test_multiple_inactive_records_allowed(self):
        """Ensure the new UniqueConstraint allows multiple inactive records for the same scope."""
        config_key = "multi.inactive"
        scope_type = "tenant"
        scope_id = "t_multi"
        
        # Create 1st, then deactivate
        ConfigInstance.objects.create(
            config_key=config_key, scope_type=scope_type, scope_id=scope_id, 
            release_version="v1", config_json={"v": 1}, is_active=False,
            base_config_id="00000000-0000-0000-0000-000000000001",
            base_release_version="v1",
            base_config_hash="dummy_hash",
        )
        # Create 2nd, then deactivate
        ConfigInstance.objects.create(
            config_key=config_key, scope_type=scope_type, scope_id=scope_id, 
            release_version="v2", config_json={"v": 2}, is_active=False,
            base_config_id="00000000-0000-0000-0000-000000000001",
            base_release_version="v1",
            base_config_hash="dummy_hash",
        )
        # Create 3rd, make it active
        ConfigInstance.objects.create(
            config_key=config_key, scope_type=scope_type, scope_id=scope_id, 
            release_version="v3", config_json={"v": 3}, is_active=True,
            base_config_id="00000000-0000-0000-0000-000000000001",
            base_release_version="v1",
            base_config_hash="dummy_hash",
        )
        
        assert ConfigInstance.objects.filter(config_key=config_key, is_active=False).count() == 2
        assert ConfigInstance.objects.filter(config_key=config_key, is_active=True).count() == 1

    def test_automatic_deactivation_on_save(self):
        """Saving a new active config manually must deactivate existing ones (backup enforcement)."""
        config_key = "auto.deactivate"
        scope_type = "tenant"
        scope_id = "t_auto"
        
        # 1. Create first active
        c1 = ConfigInstance.objects.create(
            config_key=config_key, scope_type=scope_type, scope_id=scope_id, 
            release_version="v1", config_json={"v": 1}, is_active=True,
            base_config_id="00000000-0000-0000-0000-000000000001",
            base_release_version="v1",
            base_config_hash="dummy_hash",
        )
        
        # 2. Create second active WITHOUT using the service layer
        c2 = ConfigInstance.objects.create(
            config_key=config_key, scope_type=scope_type, scope_id=scope_id, 
            release_version="v2", config_json={"v": 2}, is_active=True,
            base_config_id="00000000-0000-0000-0000-000000000001",
            base_release_version="v1",
            base_config_hash="dummy_hash",
        )
        
        c1.refresh_from_db()
        assert c1.is_active is False
        assert c2.is_active is True

    def test_block_reactivation(self):
        """Attempting to set is_active=True on an inactive record must fail."""
        c1 = ConfigInstance.objects.create(
            config_key="no.reactivate", scope_type="tenant", scope_id="t1", 
            release_version="v1", config_json={"v": 1}, is_active=False,
            base_config_id="00000000-0000-0000-0000-000000000001",
            base_release_version="v1",
            base_config_hash="dummy_hash",
        )
        
        c1.is_active = True
        with pytest.raises(ValidationError) as exc:
            c1.save()
        assert "Inactive configuration records cannot be reactivated" in str(exc.value)

    def test_oob_immutability_allows_is_active(self):
        """is_active MUST remain mutable for OOB records so they can be deactivated."""
        oob = ConfigInstance.objects.create(
            config_key="oob.key",
            scope_type="oob",
            release_version="v1",
            config_json={"initial": True},
            is_active=True
        )
        
        oob.is_active = False
        oob.save()
        oob.refresh_from_db()
        assert oob.is_active is False

    def test_create_override_view_rejects_oob(self):
        """API endpoints for overrides should block OOB creation."""
        payload = {
            "config_key": "oob.api.test",
            "scope_type": "oob",
            "release_version": "v1.0.0",
            "config_json": {"blocked": True}
        }
        response = self.api_client.post("/api/v1/config/override/", payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "OOB configs are immutable via the API" in response.data["scope_type"][0]

    def test_oob_must_not_have_scope_id(self):
        """ValidationError if OOB is created with a scope_id."""
        with pytest.raises(ValidationError) as exc:
            ConfigInstance.objects.create(
                config_key="test.key",
                scope_type="oob",
                scope_id="some_id",
                release_version="v1",
                config_json={"foo": "bar"}
            )
        assert "OOB configs must not have a scope_id" in str(exc.value)

    def test_overrides_must_have_scope_id(self):
        """ValidationError if tenant/user override is created without a scope_id."""
        with pytest.raises(ValidationError) as exc:
            ConfigInstance.objects.create(
                config_key="test.key",
                scope_type="tenant",
                scope_id=None,
                release_version="v1",
                config_json={"foo": "bar"},
                base_config_id="00000000-0000-0000-0000-000000000001",
                base_release_version="v1",
                base_config_hash="dummy_hash",
            )
        assert "scope_id is required for scope_type='tenant'" in str(exc.value)
