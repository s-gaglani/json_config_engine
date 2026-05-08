import hashlib
import json
import uuid

from django.core.cache import cache
from django.db import models

from msbc_json_config_engine.apps.config_engine.models import ConfigInstance
from msbc_json_config_engine.apps.config_engine.utils import ConfigHasher

CACHE_TIMEOUT = 300  # seconds




class ConfigResolutionService:
    """
    Service layer for all ConfigInstance read/write operations.

    Resolution hierarchy (highest → lowest priority):
        user  →  tenant  →  oob
    """

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @staticmethod
    def get_active(
        config_key: str,
        scope_type: str,
        scope_id: str | None = None,
    ) -> ConfigInstance | None:
        """
        Return the single active ConfigInstance that matches
        (config_key, scope_type, scope_id), or None if not found.
        """
        try:
            return ConfigInstance.objects.get(
                config_key=config_key,
                scope_type=scope_type,
                scope_id=scope_id,
                is_active=True,
            )
        except ConfigInstance.DoesNotExist:
            return None

    @staticmethod
    def _ptr_key(config_key: str, tenant_id: str | None, user_id: str | None) -> str:
        """Pointer key — stores the full versioned key for the given resolution inputs."""
        t = tenant_id or "none"
        u = user_id or "none"
        return f"config:ptr:{config_key}:{t}:{u}"

    @staticmethod
    def _cache_key(config_key: str, tenant_id: str | None, user_id: str | None, release_version: str) -> str:
        """Full versioned cache key that includes the resolved release version."""
        t = tenant_id or "none"
        u = user_id or "none"
        return f"config:{config_key}:{t}:{u}:{release_version}"

    @staticmethod
    def _registry_key(config_key: str, user_id: str) -> str:
        """Key for the registry of tenants that have cached a resolution for this user."""
        return f"config:registry:{config_key}:{user_id}"

    @staticmethod
    def invalidate_cache(
        config_key: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        release_version: str | None = None,
    ) -> None:
        """
        Delete both the pointer key and the full versioned key for the given
        (config_key, tenant_id, user_id) combination.

        If user_id is provided but tenant_id is None, it targets ALL tenants
        recorded in the registry for this user.
        """
        if user_id and not tenant_id:
            # BROADCAST: Invalidate across all known tenants for this user
            reg_key = ConfigResolutionService._registry_key(config_key, user_id)
            tenants = cache.get(reg_key, set())
            # Ensure we also invalidate the 'none' tenant (global user resolution)
            tenants.add(None)
            
            for t_id in tenants:
                ConfigResolutionService._invalidate_single_scope(config_key, t_id, user_id, release_version)
            
            cache.delete(reg_key)
        else:
            # SINGLE SCOPE: Just invalidate the specific combination provided
            ConfigResolutionService._invalidate_single_scope(config_key, tenant_id, user_id, release_version)

    @staticmethod
    def _invalidate_single_scope(
        config_key: str,
        tenant_id: str | None,
        user_id: str | None,
        release_version: str | None,
    ) -> None:
        """Internal helper to invalidate one specific (key, tenant, user) combination."""
        ptr_key = ConfigResolutionService._ptr_key(config_key, tenant_id, user_id)

        if release_version is None:
            release_version = cache.get(ptr_key)

        if release_version:
            full_key = ConfigResolutionService._cache_key(config_key, tenant_id, user_id, release_version)
            cache.delete(full_key)

        cache.delete(ptr_key)

    @staticmethod
    def get_effective_config(
        config_key: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """
        Resolve the effective config for a given key using the priority order:
            1. User   (scope_type='user',   scope_id=user_id)   — if user_id provided
            2. Tenant (scope_type='tenant', scope_id=tenant_id) — if tenant_id provided
            3. OOB    (scope_type='oob',    scope_id=None)

        Returns a dict:
            {
                "config":  <config_json dict>,
                "source":  "user" | "tenant" | "oob",
                "release": <release_version str>,
            }

        Results are cached for CACHE_TIMEOUT seconds (300 s by default).
        Cache uses a two-key scheme:
          - pointer key  → holds the resolved release_version string
          - full key     → holds the result dict, keyed by release_version
        This allows invalidation without knowing the release version upfront.
        Raises ConfigInstance.DoesNotExist if no OOB config exists.
        """
        ptr_key = ConfigResolutionService._ptr_key(config_key, tenant_id, user_id)

        # --- cache read: follow pointer → full key ---
        cached_release = cache.get(ptr_key)
        if cached_release is not None:
            full_key = ConfigResolutionService._cache_key(config_key, tenant_id, user_id, cached_release)
            cached = cache.get(full_key)
            if cached is not None:
                return cached
            # Pointer is stale (full key expired) — fall through to DB

        candidates = []

        if user_id is not None:
            candidates.append(("user", user_id))
        if tenant_id is not None:
            candidates.append(("tenant", tenant_id))
        candidates.append(("oob", None))

        for source, scope_id in candidates:
            instance = ConfigResolutionService.get_active(config_key, source, scope_id)
            if instance is not None:
                result = {
                    "config": instance.config_json,
                    "source": source,
                    "release": instance.release_version,
                }
                # --- cache write: store result under full key, pointer under ptr key ---
                full_key = ConfigResolutionService._cache_key(
                    config_key, tenant_id, user_id, instance.release_version
                )
                ptr_key = ConfigResolutionService._ptr_key(config_key, tenant_id, user_id)
                cache.set(full_key, result, timeout=CACHE_TIMEOUT)
                cache.set(ptr_key, instance.release_version, timeout=CACHE_TIMEOUT)

                # --- registry write: track this tenant for the user if applicable ---
                if user_id:
                    reg_key = ConfigResolutionService._registry_key(config_key, user_id)
                    tenants = cache.get(reg_key, set())
                    if tenant_id not in tenants:
                        tenants.add(tenant_id)
                        cache.set(reg_key, tenants, timeout=CACHE_TIMEOUT)

                return result

        # No OOB found — surface Django's own DoesNotExist
        raise ConfigInstance.DoesNotExist(
            f"No active OOB ConfigInstance found for config_key='{config_key}'."
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    @staticmethod
    def create_or_replace_override(
        config_key: str,
        scope_type: str,
        scope_id: str,
        config_json: dict,
        release_version: str,
        base_config_id: uuid.UUID | None = None,
        base_release_version: str | None = None,
        parent_config_instance_id: uuid.UUID | None = None,
    ) -> ConfigInstance:
        """
        Deactivate any existing active config for (config_key, scope_type, scope_id),
        resolve the OOB base for hash calculation, then create and return a new
        active ConfigInstance.

        If base_config_id is not provided it is auto-resolved from the current
        active OOB config for the same config_key.
        """
        # 1. Deactivate existing active override(s) for this scope
        ConfigInstance.objects.filter(
            config_key=config_key,
            scope_type=scope_type,
            scope_id=scope_id,
            is_active=True,
        ).update(is_active=False)

        # 2. Resolve OOB base for lineage / hash
        oob_instance = ConfigResolutionService.get_active(
            config_key, scope_type="oob", scope_id=None
        )

        if base_config_id is None and oob_instance is not None:
            base_config_id = oob_instance.id

        if base_release_version is None and oob_instance is not None:
            base_release_version = oob_instance.release_version

        base_config_hash = (
            ConfigHasher.generate_hash(oob_instance.config_json)
            if oob_instance is not None
            else None
        )

        # 3. Create the new active instance
        instance = ConfigInstance.objects.create(
            config_key=config_key,
            scope_type=scope_type,
            scope_id=scope_id,
            config_json=config_json,
            release_version=release_version,
            base_config_id=base_config_id,
            base_release_version=base_release_version,
            base_config_hash=base_config_hash,
            parent_config_instance_id=parent_config_instance_id,
            is_active=True,
        )

        # 4. Invalidate cached resolution for the affected scope
        tenant_id = scope_id if scope_type == "tenant" else None
        user_id   = scope_id if scope_type == "user"   else None
        ConfigResolutionService.invalidate_cache(config_key, tenant_id=tenant_id, user_id=user_id)

        return instance

    @staticmethod
    def reset_to_oob(
        config_key: str,
        scope_type: str,
        scope_id: str,
    ) -> None:
        """
        Deactivate all active overrides for (config_key, scope_type, scope_id),
        effectively falling the scope back to OOB resolution.
        """
        ConfigInstance.objects.filter(
            config_key=config_key,
            scope_type=scope_type,
            scope_id=scope_id,
            is_active=True,
        ).update(is_active=False)

        # Invalidate cached resolution for the affected scope
        tenant_id = scope_id if scope_type == "tenant" else None
        user_id   = scope_id if scope_type == "user"   else None
        ConfigResolutionService.invalidate_cache(config_key, tenant_id=tenant_id, user_id=user_id)

    # ------------------------------------------------------------------
    # Drift / staleness detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect_outdated_tenant_configs() -> models.QuerySet:
        """
        Return all active tenant ConfigInstances whose base_config_id no longer
        matches the current active OOB config for the same config_key.

        A tenant config is considered outdated when the OOB it was derived from
        has since been superseded by a newer OOB version.
        """
        # Build a subquery: for each config_key, find the id of the active OOB config.
        oob_qs = (
            ConfigInstance.objects.filter(
                scope_type="oob",
                is_active=True,
            )
            .values("config_key")
            .annotate(current_oob_id=models.F("id"))
        )

        current_oob_by_key: dict[str, uuid.UUID] = {
            row["config_key"]: row["current_oob_id"] for row in oob_qs
        }

        if not current_oob_by_key:
            return ConfigInstance.objects.none()

        # Return tenant configs whose stored base_config_id doesn't match
        from django.db.models import Q

        outdated_filter = Q()
        for config_key, current_oob_id in current_oob_by_key.items():
            outdated_filter |= Q(
                config_key=config_key,
                scope_type="tenant",
                is_active=True,
            ) & ~Q(base_config_id=current_oob_id)

        return ConfigInstance.objects.filter(outdated_filter)

    @staticmethod
    def detect_drift(tenant_instance: ConfigInstance) -> bool:
        """
        Return True if the tenant config's base_config_hash differs from the
        SHA-256 hash of the *current* active OOB config_json for the same key.

        This catches cases where the OOB payload changed but the tenant config
        was not re-evaluated (even if the OOB id is the same, unlikely but
        possible in manual DB edits or test scenarios).
        """
        oob_instance = ConfigResolutionService.get_active(
            tenant_instance.config_key, scope_type="oob", scope_id=None
        )

        if oob_instance is None:
            # No OOB to compare against; treat as drifted
            return True

        current_oob_hash = ConfigHasher.generate_hash(oob_instance.config_json)
        return tenant_instance.base_config_hash != current_oob_hash
