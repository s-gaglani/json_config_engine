import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.utils import timezone
from django.contrib.auth.models import User
from apps.config_engine.models import ConfigInstance
from apps.config_engine.services import ConfigResolutionService

# 1. Create superuser
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
    print("Superuser created.")

# 2. Clear existing data for clean state
ConfigInstance.objects.all().delete()

# 3. Create OOB v1
oob_v1 = ConfigInstance.objects.create(
    config_key="ui.theme",
    scope_type="oob",
    scope_id=None,
    release_version="v1.0.0",
    config_json={"color": "blue", "font": "sans-serif"},
    is_active=True
)
print("OOB v1 created.")

# 4. Create tenant override based on v1
tenant_config = ConfigResolutionService.create_or_replace_override(
    config_key="ui.theme",
    scope_type="tenant",
    scope_id="tenant_a",
    config_json={"color": "darkblue", "font": "sans-serif"},
    release_version="v1.0.0",
    base_config_id=oob_v1.id,
    base_release_version="v1.0.0"
)
print("Tenant override created.")

# 5. Create OOB v2 (this will deactivate v1)
# Using management command style logic manually
ConfigInstance.objects.filter(config_key="ui.theme", scope_type="oob", is_active=True).update(is_active=False)

oob_v2 = ConfigInstance.objects.create(
    config_key="ui.theme",
    scope_type="oob",
    scope_id=None,
    release_version="v2.0.0",
    config_json={"color": "blue", "font": "inter", "grid": True},
    is_active=True
)
print("OOB v2 created. Tenant override is now outdated.")
