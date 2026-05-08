"""
Production settings – security-hardened overrides over base settings.
All sensitive values come from environment variables.
"""
from decouple import Csv, config

from .base import *  # noqa: F401, F403

DEBUG = False

ALLOWED_HOSTS = config("ALLOWED_HOSTS", cast=Csv())

# ---------------------------------------------------------------------------
# HTTPS / security hardening
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# Production logging: INFO level only
# ---------------------------------------------------------------------------
LOGGING["root"]["level"] = "INFO"  # noqa: F405
