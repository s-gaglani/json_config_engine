import json

from django.contrib import admin, messages
from django.contrib.admin import ModelAdmin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.http import urlencode

from msbc_json_config_engine.apps.config_engine.models import ConfigInstance
from msbc_json_config_engine.apps.config_engine.services import ConfigResolutionService


@admin.register(ConfigInstance)
class ConfigInstanceAdmin(ModelAdmin):
    # ------------------------------------------------------------------
    # Custom change-form template (JSON editor + client-side validation)
    # ------------------------------------------------------------------
    change_form_template = (
        "admin/config_engine/configinstance/change_form.html"
    )

    # ------------------------------------------------------------------
    # List view
    # ------------------------------------------------------------------
    list_display = (
        "id",
        "config_key",
        "scope_type",
        "scope_id",
        "release_version",
        "is_active",
        "created_at",
        "lineage_link",
        "diff_link",
    )
    list_filter = ("scope_type", "is_active", "release_version", "base_config_id")
    search_fields = ("config_key", "scope_id")
    ordering = ("-created_at",)

    # ------------------------------------------------------------------
    # Detail view
    # ------------------------------------------------------------------
    readonly_fields = ("id", "base_config_hash", "created_at", "updated_at")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    actions = ("mark_as_inactive", "reset_selected_to_oob")

    @admin.action(description="Mark selected as inactive")
    def mark_as_inactive(self, request, queryset):
        """
        Deactivate selected overrides. Skips OOB records with a warning.
        """
        oob_count = queryset.filter(scope_type="oob").count()
        updated = queryset.exclude(scope_type="oob").update(is_active=False)
        
        if oob_count:
            self.message_user(
                request,
                f"{oob_count} OOB record(s) were skipped — OOB is_active cannot be modified.",
                level=messages.WARNING
            )
        self.message_user(request, f"{updated} config instance(s) marked as inactive.")

    @admin.action(description="Reset selected to OOB")
    def reset_selected_to_oob(self, request, queryset):
        """
        For each selected record:
          - Skip OOB configs with a warning.
          - Deactivate all active overrides for (config_key, scope_type, scope_id).
        """
        reset_count = 0
        skipped = 0

        for obj in queryset:
            if obj.scope_type == "oob":
                skipped += 1
                continue
            ConfigResolutionService.reset_to_oob(
                config_key=obj.config_key,
                scope_type=obj.scope_type,
                scope_id=obj.scope_id,
            )
            reset_count += 1

        if reset_count:
            self.message_user(
                request,
                f"{reset_count} config(s) reset to OOB.",
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} OOB config(s) skipped — OOB records cannot be reset.",
                level=messages.WARNING,
            )

    # ------------------------------------------------------------------
    # OOB immutability — block delete
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Immutability enforcement in Admin — block delete
    # ------------------------------------------------------------------
    def delete_model(self, request, obj):
        """Block deletion of all config instances."""
        raise PermissionDenied(
            "Config instances are immutable and cannot be deleted once created."
        )

    def delete_queryset(self, request, queryset):
        """Block bulk deletion of all config instances."""
        raise PermissionDenied(
            "Config instances are immutable and cannot be deleted once created."
        )

    def has_delete_permission(self, request, obj=None):
        """Disable the delete button for all config records."""
        return False

    # ------------------------------------------------------------------
    # Immutability enforcement in Admin
    # ------------------------------------------------------------------
    def get_readonly_fields(self, request, obj=None):
        """
        Make all core fields read-only for existing objects to enforce the
        replace-only model in the UI.
        """
        base_readonly = list(self.readonly_fields)
        if obj and obj.scope_type == "oob":
            if "is_active" not in base_readonly:
                base_readonly.append("is_active")

        if obj:  # editing an existing object
            return tuple(base_readonly) + (
                "config_key",
                "scope_type",
                "scope_id",
                "release_version",
                "config_json",
                "base_config_id",
                "base_release_version",
                "parent_config_instance_id",
            )
        return tuple(base_readonly)

    def has_change_permission(self, request, obj=None):
        """
        Allow change permission for all records so is_active can be toggled,
        while other fields are protected by get_readonly_fields().
        """
        return super().has_change_permission(request, obj)

    # ------------------------------------------------------------------
    # Changelist override — outdated-configs banner
    # ------------------------------------------------------------------
    def changelist_view(self, request, extra_context=None):
        outdated_qs = ConfigResolutionService.detect_outdated_tenant_configs()
        count = outdated_qs.count()
        if count > 0:
            self.message_user(
                request,
                (
                    f"⚠ {count} tenant config(s) are outdated and based on a "
                    "superseded OOB release. Use the 'Reset selected to OOB' "
                    "action or review the Diff Viewer."
                ),
                level=messages.WARNING,
            )
        return super().changelist_view(request, extra_context=extra_context)

    # ------------------------------------------------------------------
    # Custom URLs — diff view
    # ------------------------------------------------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<uuid:pk>/diff/",
                self.admin_site.admin_view(self.diff_view),
                name="config_engine_configinstance_diff",
            ),
        ]
        return custom + urls

    def diff_view(self, request, pk):
        """Side-by-side JSON diff: this config vs (1) its own base and (2) latest OOB."""
        obj = get_object_or_404(ConfigInstance, pk=pk)

        # 1. Base config (the parent this was cloned from)
        base_obj = None
        if obj.base_config_id:
            try:
                base_obj = ConfigInstance.objects.get(pk=obj.base_config_id)
            except ConfigInstance.DoesNotExist:
                pass

        # 2. Latest OOB config (the current active OOB for this key)
        latest_oob = ConfigResolutionService.get_active(
            config_key=obj.config_key,
            scope_type="oob",
            scope_id=None,
        )

        is_drifted = ConfigResolutionService.detect_drift(obj)
        # Outdated if our base is not the current active OOB
        is_outdated = (
            latest_oob is not None and obj.base_config_id != latest_oob.id
        )

        context = {
            **self.admin_site.each_context(request),
            "obj": obj,
            "base_obj": base_obj,
            "latest_oob": latest_oob,
            "this_json": json.dumps(obj.config_json, indent=2, sort_keys=True),
            "base_json": (
                json.dumps(base_obj.config_json, indent=2, sort_keys=True)
                if base_obj
                else ""
            ),
            "latest_oob_json": (
                json.dumps(latest_oob.config_json, indent=2, sort_keys=True)
                if latest_oob
                else ""
            ),
            "is_drifted": is_drifted,
            "is_outdated": is_outdated,
            "title": f"Config Diff — {obj.config_key}",
        }
        return render(
            request,
            "admin/config_engine/configinstance/diff_view.html",
            context,
        )

    # ------------------------------------------------------------------
    # Custom list columns
    # ------------------------------------------------------------------
    @admin.display(description="Lineage")
    def lineage_link(self, obj):
        """
        Renders a clickable 'View lineage' link that filters the changelist
        to all records sharing the same base_config_id, making it easy to
        trace the full override tree for a given OOB base.
        """
        if not obj.base_config_id:
            return "—"

        qs = urlencode({"base_config_id": str(obj.base_config_id)})
        url = f"../config_engine/configinstance/?{qs}"
        return format_html('<a href="{}">View lineage</a>', url)

    @admin.display(description="Diff")
    def diff_link(self, obj):
        """Renders a 'View Diff' link to the custom diff view for this record."""
        url = reverse(
            "admin:config_engine_configinstance_diff",
            args=[obj.pk],
        )
        return format_html('<a href="{}">View Diff</a>', url)
