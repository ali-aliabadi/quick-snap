"""Django settings for quicksnap — env-driven, S3 in prod / local media in dev."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    USE_S3=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    SECRET_KEY=(str, "dev-insecure-change-me"),
)
# Read .env if present (dev). In prod, real env vars take precedence.
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Prometheus scrapes the app over the compose network as http://web:8000/metrics,
# so Django must accept "web" as a Host or it answers 400 and every dashboard
# panel is empty. This is a container-internal service name — it is not routable
# from outside the compose network, so accepting it costs nothing publicly.
if "web" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS = list(ALLOWED_HOSTS) + ["web"]
# CSRF trusted origins (https://your-domain) for prod behind Caddy.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "storages",
    "snap",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Outermost-but-last so it times the full stack below it.
    "snap.middleware.MetricsMiddleware",
]

ROOT_URLCONF = "quicksnap.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "snap.context_processors.ui_strings",
            ],
        },
    },
]

WSGI_APPLICATION = "quicksnap.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}

# SQLite under concurrent guests + background email threads locks easily.
# A busy timeout lets writers wait instead of failing with "database is locked"
# (WAL mode is enabled in snap.apps.ready).
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].setdefault("timeout", 20)

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Cache: backs the failed-join throttle (snap/throttle.py) ---
# This has to be shared across processes. The default LocMemCache is per-process,
# so with 3 gunicorn workers each would see only its own third of the failures
# and the throttle would be ~3x looser than configured. The database backend is
# shared, needs no extra service, and its table is created on boot by
# deploy/entrypoint.sh. Writes are tiny and only happen on failures.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "quicksnap_cache",
        "OPTIONS": {"MAX_ENTRIES": 5000, "CULL_FREQUENCY": 3},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

# Default UI language for the guest-facing app: "fa" (Persian, RTL) or "en".
# Guests are Persian-speaking, so the app opens in Persian; a per-visitor
# `ui_lang` cookie (set via the header language toggle) overrides this.
APP_LANG = env("APP_LANG", default="fa")

# --- Static ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Sessions: long-lived so a guest's roll survives a ~6h event ---
SESSION_COOKIE_AGE = 8 * 3600  # 8h > typical wedding
SESSION_SAVE_EVERY_REQUEST = True  # activity refreshes expiry
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# --- Storage: S3 in prod, local media in dev (USE_S3 flag) ---
if env("USE_S3"):
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default=None)
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)  # for S3-compatible
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
else:
    MEDIA_URL = "media/"
    MEDIA_ROOT = BASE_DIR / "media"

# --- Email: console in dev, SMTP in prod ---
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="Quick Snap <noreply@example.com>"
)

# Uploaded photo cap (defensive; camera JPEGs are small).
DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024  # 15 MB

# --- Metrics ---
# Shared secret for GET /metrics. Empty means the endpoint 404s in production
# (fail closed) and is open in DEBUG. Generate one with:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
METRICS_TOKEN = env("METRICS_TOKEN", default="")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Security (prod) ---
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env.bool(
        "SECURE_SSL_REDIRECT", default=False
    )  # Caddy handles TLS
