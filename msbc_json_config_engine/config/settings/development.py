"""
Development settings – extends base settings with debug-friendly overrides.
"""
from decouple import config

from .base import *  # noqa: F401, F403

DEBUG = config("DEBUG", default=True, cast=bool)

# In development only: allow all hosts if DEBUG is True
if DEBUG:
    ALLOWED_HOSTS = ["*"]

# Disable HTTPS requirements in development
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Show detailed errors
LOGGING["root"]["level"] = "DEBUG"  # noqa: F405

# Allow browsable API renderer in development
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]
