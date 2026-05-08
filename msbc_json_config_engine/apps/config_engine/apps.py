from django.apps import AppConfig


class ConfigEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "msbc_json_config_engine.apps.config_engine"
    label = "config_engine"
    verbose_name = "Config Engine"
