import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, UniqueConstraint

from msbc_json_config_engine.apps.config_engine.utils import ConfigHasher


class ConfigInstance(models.Model):
    SCOPE_TYPE_CHOICES = [
        ("oob", "Out-of-Box"),
        ("tenant", "Tenant"),
        ("user", "User"),
    ]

    # Primary key
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Core fields
    config_key = models.CharField(max_length=255)
    scope_type = models.CharField(max_length=10, choices=SCOPE_TYPE_CHOICES)
    scope_id = models.CharField(max_length=255, null=True, blank=True)
    release_version = models.CharField(max_length=50)

    # Lineage tracking
    base_config_id = models.UUIDField(null=True, blank=True)
    base_release_version = models.CharField(max_length=50, null=True, blank=True)
    base_config_hash = models.CharField(max_length=64, null=True, blank=True)
    parent_config_instance_id = models.UUIDField(null=True, blank=True)

    # Config payload
    config_json = models.JSONField()

    # Status & timestamps
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "config_instances"
        constraints = [
            UniqueConstraint(
                fields=["config_key", "scope_type", "scope_id"],
                condition=Q(is_active=True),
                name="unique_active_config",
            ),
            UniqueConstraint(
                fields=["config_key", "release_version"],
                condition=Q(scope_type="oob"),
                name="unique_oob_config",
            )
        ]
        indexes = [
            models.Index(
                fields=["config_key", "scope_type", "scope_id", "is_active"],
                name="idx_config_lookup",
            ),
            models.Index(
                fields=["release_version"],
                name="idx_release",
            ),
            models.Index(
                fields=["base_config_id"],
                name="idx_base_config",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.config_key} [{self.scope_type}:{self.scope_id}] v{self.release_version}"

    def clean(self):
        if self.scope_type == "oob":
            if self.scope_id:
                raise ValidationError("OOB configs must not have a scope_id.")
        else:
            if not self.scope_id:
                raise ValidationError(
                    f"scope_id is required for scope_type='{self.scope_type}'."
                )

            # FIX 1: Enforce lineage fields on overrides
            missing = []
            if not self.base_config_id:
                missing.append("base_config_id")
            if not self.base_release_version:
                missing.append("base_release_version")
            if missing:
                raise ValidationError(
                    f"Overrides of scope_type '{self.scope_type}' must include lineage fields: "
                    f"{', '.join(missing)}"
                )

        if self.scope_type == "user" and not self.parent_config_instance_id:
            raise ValidationError(
                "User overrides must include parent_config_instance_id."
            )

    def save(self, *args, **kwargs):
        if self.is_active:
            # Backup enforcement: Deactivate any other active records in this scope
            # before validation and saving. This ensures the UniqueConstraint
            # is not violated and handles the "replace-only" logic automatically.
            ConfigInstance.objects.filter(
                config_key=self.config_key,
                scope_type=self.scope_type,
                scope_id=self.scope_id,
                is_active=True,
            ).exclude(pk=self.pk).update(is_active=False)

        # Ensure lineage hash is populated if missing before validation/saving
        if (
            self.scope_type in ("tenant", "user")
            and self.base_config_id
            and not self.base_config_hash
        ):
            base_oob = ConfigInstance.objects.filter(id=self.base_config_id).first()
            if base_oob:
                self.base_config_hash = ConfigHasher.generate_hash(base_oob.config_json)

        self.full_clean()

        if not self._state.adding:
            # This is an update
            original = ConfigInstance.objects.get(pk=self.id)
            
            # Core fields are immutable once created across ALL scopes
            immutable_fields = (
                "config_key", "scope_type", "scope_id", "release_version", "config_json",
                "base_config_id", "base_release_version", "base_config_hash", "parent_config_instance_id"
            )
            for field in immutable_fields:
                if getattr(self, field) != getattr(original, field):
                    raise ValidationError(
                        f"ConfigInstance is immutable. Field '{field}' cannot be changed after creation."
                    )

            # Status transition: Block reactivation (False -> True)
            if original.is_active is False and self.is_active is True:
                raise ValidationError(
                    "Inactive configuration records cannot be reactivated. Please create a new override."
                )

        super().save(*args, **kwargs)
