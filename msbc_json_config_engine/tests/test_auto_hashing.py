import pytest
from apps.config_engine.models import ConfigInstance
from apps.config_engine.utils import ConfigHasher
from rest_framework.test import APIClient
from rest_framework import status

@pytest.mark.django_db
class TestAutoHashing:
    def setup_method(self):
        self.api_client = APIClient()
        self.oob = ConfigInstance.objects.create(
            config_key="test.key",
            scope_type="oob",
            release_version="v1.0.0",
            config_json={"foo": "bar"},
            is_active=True
        )

    def test_orm_auto_hashing(self):
        """Creating an override via ORM without hash should auto-populate it."""
        override = ConfigInstance.objects.create(
            config_key="test.key",
            scope_type="tenant",
            scope_id="tenant1",
            base_config_id=self.oob.id,
            base_release_version=self.oob.release_version,
            config_json={"foo": "overridden"},
            release_version="v1.0.0"
        )
        assert override.base_config_hash is not None
        assert override.base_config_hash == ConfigHasher.generate_hash(self.oob.config_json)

    def test_api_auto_hashing(self):
        """Creating an override via API without hash should auto-populate it."""
        payload = {
            "config_key": "test.key",
            "scope_type": "tenant",
            "scope_id": "tenant1",
            "base_config_id": str(self.oob.id),
            "base_release_version": self.oob.release_version,
            "config_json": {"foo": "overridden"},
            "release_version": "v1.0.0"
        }
        response = self.api_client.post("/api/v1/config/override/", payload, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["base_config_hash"] == ConfigHasher.generate_hash(self.oob.config_json)

    def test_drift_detection_with_auto_hash(self):
        """Verify that drift detection still works correctly with auto-populated hash."""
        override = ConfigInstance.objects.create(
            config_key="test.key",
            scope_type="tenant",
            scope_id="tenant1",
            base_config_id=self.oob.id,
            base_release_version=self.oob.release_version,
            config_json={"foo": "overridden"},
            release_version="v1.0.0"
        )
        
        # Initial check: no drift
        response = self.api_client.get("/api/v1/config/diff/", {
            "config_key": "test.key",
            "scope_type": "tenant",
            "scope_id": "tenant1"
        })
        assert response.data["is_drifted"] is False
        
        # Deactivate old OOB, create new one with different JSON to simulate OOB change
        self.oob.is_active = False
        self.oob.save()
        ConfigInstance.objects.create(
            config_key="test.key",
            scope_type="oob",
            release_version="v2.0.0",
            config_json={"foo": "changed_base"},
            is_active=True
        )
        
        response = self.api_client.get("/api/v1/config/diff/", {
            "config_key": "test.key",
            "scope_type": "tenant",
            "scope_id": "tenant1"
        })
        assert response.data["is_drifted"] is True
