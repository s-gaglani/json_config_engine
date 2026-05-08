from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from msbc_json_config_engine.apps.config_engine.models import ConfigInstance
from msbc_json_config_engine.apps.config_engine.serializers import ConfigInstanceSerializer
from msbc_json_config_engine.apps.config_engine.services import ConfigResolutionService
from msbc_json_config_engine.apps.config_engine.utils import ConfigHasher

# ---------------------------------------------------------------------------
# Inline response schemas for endpoints that return free-form dicts
# ---------------------------------------------------------------------------
_EFFECTIVE_CONFIG_RESPONSE = {
    "type": "object",
    "properties": {
        "config":   {"type": "object"},
        "source":   {"type": "string", "enum": ["user", "tenant", "oob"]},
        "release":  {"type": "string"},
    },
}

_DIFF_RESPONSE = {
    "type": "object",
    "properties": {
        "current":     {"type": "object"},
        "oob":         {"type": "object"},
        "is_drifted":  {"type": "boolean"},
        "is_outdated": {"type": "boolean"},
    },
}


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@extend_schema(
    summary="Get effective config",
    description=(
        "Resolves and returns the active config for the given key. "
        "Resolution order: User → Tenant → OOB."
    ),
    parameters=[
        OpenApiParameter("key",       OpenApiTypes.STR, required=True,  description="Config key e.g. invoice.form"),
        OpenApiParameter("tenant_id", OpenApiTypes.STR, required=False, description="Tenant scope ID"),
        OpenApiParameter("user_id",   OpenApiTypes.STR, required=False, description="User scope ID"),
    ],
    responses={
        200: OpenApiResponse(response=_EFFECTIVE_CONFIG_RESPONSE, description="Resolved config with source and release"),
        404: OpenApiResponse(description="No active OOB config found for key"),
    },
)
class GetEffectiveConfigView(APIView):
    """
    GET /api/v1/config/
    Query params: key (required), tenant_id (optional), user_id (optional)
    """

    def get(self, request: Request) -> Response:
        config_key = request.query_params.get("key")
        if not config_key:
            return Response(
                {"detail": "'key' query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant_id = request.query_params.get("tenant_id")
        user_id = request.query_params.get("user_id")

        try:
            result = ConfigResolutionService.get_effective_config(
                config_key=config_key,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        except ConfigInstance.DoesNotExist:
            return Response(
                {"detail": f"No active OOB config found for key '{config_key}'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(result, status=status.HTTP_200_OK)


@extend_schema(
    summary="Create or replace a config override",
    description=(
        "Creates a tenant or user override. Automatically deactivates the previous override "
        "for the same scope. Standardizes on using 'scope_id' (e.g. tenant_acme) regardless of type."
    ),
    request=ConfigInstanceSerializer,
    responses={
        201: ConfigInstanceSerializer,
        400: OpenApiResponse(description="Validation error (e.g. missing scope_id or OOB scope rejected)"),
    },
)
class CreateOverrideView(APIView):
    """
    POST /api/v1/config/override/
    Body: { config_key, scope_type, scope_id, config_json, release_version, ... }
    """

    def post(self, request: Request) -> Response:
        serializer = ConfigInstanceSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        valid_data = serializer.validated_data

        instance = ConfigResolutionService.create_or_replace_override(
            config_key=valid_data["config_key"],
            scope_type=valid_data["scope_type"],
            scope_id=valid_data["scope_id"],
            config_json=valid_data["config_json"],
            release_version=valid_data["release_version"],
            base_config_id=valid_data.get("base_config_id"),
            base_release_version=valid_data.get("base_release_version"),
            parent_config_instance_id=valid_data.get("parent_config_instance_id"),
        )

        return Response(
            ConfigInstanceSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    summary="Reset override to OOB",
    description=(
        "Deactivates all active overrides for the given config key and scope, "
        "falling back to OOB resolution."
    ),
    request={
        "application/json": {
            "type": "object",
            "required": ["config_key", "scope_type", "scope_id"],
            "properties": {
                "config_key":  {"type": "string"},
                "scope_type":  {"type": "string", "enum": ["tenant", "user"]},
                "scope_id":    {"type": "string"},
            },
        }
    },
    responses={
        204: OpenApiResponse(description="Override successfully deactivated — falls back to OOB"),
        400: OpenApiResponse(description="Missing required fields"),
    },
)
class ResetToOOBView(APIView):
    """
    POST /api/v1/config/reset/
    Body: { config_key, scope_type, scope_id }
    """

    def post(self, request: Request) -> Response:
        scope_type = request.data.get("scope_type")
        if scope_type == "oob":
            return Response(
                {
                    "error": "OOB configs cannot be reset. They are managed via the release pipeline only."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = request.data
        missing = [
            f for f in ("config_key", "scope_type", "scope_id") if not data.get(f)
        ]
        if missing:
            return Response(
                {"detail": f"Missing required fields: {missing}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ConfigResolutionService.reset_to_oob(
            config_key=data["config_key"],
            scope_type=data["scope_type"],
            scope_id=data["scope_id"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    summary="Get config lineage",
    description=(
        "Returns the full history of all config instances (active and inactive) "
        "for a given config key across all scopes."
    ),
    parameters=[
        OpenApiParameter("config_key", OpenApiTypes.STR, required=True, description="Config key to retrieve lineage for"),
    ],
    responses={
        200: ConfigInstanceSerializer(many=True),
        400: OpenApiResponse(description="Missing config_key parameter"),
    },
)
class GetLineageView(APIView):
    """
    GET /api/v1/config/lineage/
    Query param: config_key (required)
    Returns all ConfigInstances for this key across all scopes and activity states.
    """

    def get(self, request: Request) -> Response:
        config_key = request.query_params.get("config_key")
        if not config_key:
            return Response(
                {"detail": "'config_key' query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instances = (
            ConfigInstance.objects.filter(config_key=config_key)
            .order_by("created_at")
        )
        serializer = ConfigInstanceSerializer(instances, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    summary="Diff config against current OOB",
    description=(
        "Compares a specific config instance against the currently active OOB config. "
        "Returns drift and outdated status."
    ),
    parameters=[
        OpenApiParameter("config_key",  OpenApiTypes.STR, required=True,  description="Config key"),
        OpenApiParameter("scope_type",  OpenApiTypes.STR, required=True,  description="Scope type: tenant or user"),
        OpenApiParameter("scope_id",    OpenApiTypes.STR, required=False, description="Scope ID (tenant or user ID)"),
    ],
    responses={
        200: OpenApiResponse(response=_DIFF_RESPONSE, description="Diff result with drift and outdated flags"),
        404: OpenApiResponse(description="No active config found for the given scope"),
    },
)
class DiffConfigView(APIView):
    """
    GET /api/v1/config/diff/
    Query params: config_key, scope_type, scope_id
    Returns the requested config alongside the active OOB config plus drift/outdated flags.
    """

    def get(self, request: Request) -> Response:
        config_key = request.query_params.get("config_key")
        scope_type = request.query_params.get("scope_type")
        scope_id = request.query_params.get("scope_id") or None

        missing = [
            p for p in ("config_key", "scope_type")
            if not request.query_params.get(p)
        ]
        if missing:
            return Response(
                {"detail": f"Missing required query params: {missing}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target = ConfigResolutionService.get_active(
            config_key=config_key,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        if target is None:
            return Response(
                {"detail": "No active config found for the given scope."},
                status=status.HTTP_404_NOT_FOUND,
            )

        oob_instance = ConfigResolutionService.get_active(
            config_key=config_key,
            scope_type="oob",
            scope_id=None,
        )

        is_drifted = ConfigResolutionService.detect_drift(target) if oob_instance else False

        # Outdated: base_config_id no longer matches the current OOB id
        is_outdated = (
            oob_instance is not None
            and target.base_config_id != oob_instance.id
        )

        return Response(
            {
                "current": ConfigInstanceSerializer(target).data,
                "oob": ConfigInstanceSerializer(oob_instance).data if oob_instance else None,
                "is_drifted": is_drifted,
                "is_outdated": is_outdated,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    summary="List outdated tenant configs",
    description=(
        "Returns all active tenant configs whose base OOB has been superseded "
        "by a newer release."
    ),
    responses={200: ConfigInstanceSerializer(many=True)},
)
class OutdatedConfigsView(APIView):
    """
    GET /api/v1/config/outdated/
    Returns all active tenant configs that are outdated relative to their OOB base.
    """

    def get(self, request: Request) -> Response:
        qs = ConfigResolutionService.detect_outdated_tenant_configs()
        serializer = ConfigInstanceSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


