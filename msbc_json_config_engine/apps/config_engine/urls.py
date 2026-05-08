from django.urls import path

from apps.config_engine.views import (
    CreateOverrideView,
    DiffConfigView,
    GetEffectiveConfigView,
    GetLineageView,
    OutdatedConfigsView,
    ResetToOOBView,
)

urlpatterns = [
    # GET  /api/v1/config/
    path("config/", GetEffectiveConfigView.as_view(), name="config-effective"),

    # POST /api/v1/config/override/
    path("config/override/", CreateOverrideView.as_view(), name="config-override"),

    # POST /api/v1/config/reset/
    path("config/reset/", ResetToOOBView.as_view(), name="config-reset"),

    # GET  /api/v1/config/lineage/
    path("config/lineage/", GetLineageView.as_view(), name="config-lineage"),

    # GET  /api/v1/config/diff/
    path("config/diff/", DiffConfigView.as_view(), name="config-diff"),

    # GET  /api/v1/config/outdated/
    path("config/outdated/", OutdatedConfigsView.as_view(), name="config-outdated"),
]
