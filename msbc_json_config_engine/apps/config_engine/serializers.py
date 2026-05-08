from rest_framework import serializers

from msbc_json_config_engine.apps.config_engine.models import ConfigInstance


class ConfigInstanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConfigInstance
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")
        validators = []  # Let model.full_clean() and DB handle constraints

    def validate_scope_type(self, value):
        if value == "oob":
            raise serializers.ValidationError("OOB configs are immutable via the API.")
        return value

    def validate(self, data):
        scope_type = data.get("scope_type")
        scope_id = data.get("scope_id")

        if scope_type in ("tenant", "user") and not scope_id:
            raise serializers.ValidationError(
                {"scope_id": f"scope_id is required for scope_type='{scope_type}'."}
            )

        if scope_type == "oob" and scope_id:
            # Although oob is blocked in validate_scope_type, this is good for completeness
            raise serializers.ValidationError(
                {"scope_id": "OOB configs must not have a scope_id."}
            )

        # FIX 1: Enforce lineage fields on overrides
        if scope_type in ("tenant", "user"):
            for field in ("base_config_id", "base_release_version"):
                if not data.get(field):
                    raise serializers.ValidationError(
                        {field: f"This field is required for scope_type '{scope_type}'."}
                    )

        if scope_type == "user" and not data.get("parent_config_instance_id"):
            raise serializers.ValidationError(
                {"parent_config_instance_id": "This field is required for user overrides."}
            )

        return data
