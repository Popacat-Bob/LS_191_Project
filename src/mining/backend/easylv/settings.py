"""Django settings for the EasyLV backend."""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "accounts",
    "inventory",
    "mining",
    "orders",
    "invoicing",
    "activitylog",
    "integrations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "easylv.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "easylv.wsgi.application"
ASGI_APPLICATION = "easylv.asgi.application"

DATABASES = {"default": env.db("DATABASE_URL")}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "easylv.api.DefaultPagination",
    "EXCEPTION_HANDLER": "easylv.api.api_exception_handler",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"demo_console": {"format": "%(message)s"}},
    "handlers": {
        "demo_console": {
            "class": "logging.StreamHandler",
            "formatter": "demo_console",
        }
    },
    "root": {"handlers": ["demo_console"], "level": "WARNING"},
    "loggers": {
        "invoicing": {
            "level": "INFO",
            "propagate": True,
        },
        "invoicing.senders": {
            "level": "INFO",
            "propagate": True,
        },
    },
}

COMMENT_SOURCE = env("COMMENT_SOURCE", default="simulated")
FB_APP_SECRET = env("FB_APP_SECRET", default="")
FB_VERIFY_TOKEN = env("FB_VERIFY_TOKEN", default="")
FB_PAGE_ACCESS_TOKEN = env("FB_PAGE_ACCESS_TOKEN", default="")
PAYMONGO_SECRET_KEY = env("PAYMONGO_SECRET_KEY", default="")
PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", default="fake")
MESSAGE_SENDER = env("MESSAGE_SENDER", default="console")
INVOICE_DELAY_SECONDS = env.int("INVOICE_DELAY_SECONDS", default=0)
MINING_PATTERN = env(
    "MINING_PATTERN",
    default=r"^\s*(?:mine\s*)?((?!mine$)[a-z0-9][a-z0-9-]*)\s*$",
)
PAYMENT_WINDOW_HOURS = env.int("PAYMENT_WINDOW_HOURS", default=24)
INVOICE_RECONCILIATION_INTERVAL_SECONDS = env.int(
    "INVOICE_RECONCILIATION_INTERVAL_SECONDS",
    default=30,
)
INVOICE_RECONCILIATION_GRACE_SECONDS = env.int(
    "INVOICE_RECONCILIATION_GRACE_SECONDS",
    default=60,
)

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_TIMEZONE = TIME_ZONE
_configured_visibility_timeout = env.int("CELERY_VISIBILITY_TIMEOUT", default=0)
CELERY_VISIBILITY_TIMEOUT = _configured_visibility_timeout or max(
    3600,
    INVOICE_DELAY_SECONDS + 3600,
)
if CELERY_VISIBILITY_TIMEOUT <= INVOICE_DELAY_SECONDS:
    raise ImproperlyConfigured(
        "CELERY_VISIBILITY_TIMEOUT must exceed INVOICE_DELAY_SECONDS."
    )
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": CELERY_VISIBILITY_TIMEOUT,
}
CELERY_WORKER_LOG_FORMAT = env(
    "CELERY_WORKER_LOG_FORMAT",
    default="%(asctime)s %(levelname)s/%(processName)s: %(message)s",
)
CELERY_WORKER_TASK_LOG_FORMAT = env(
    "CELERY_WORKER_TASK_LOG_FORMAT",
    default="%(asctime)s %(levelname)s/%(processName)s: %(message)s",
)
CELERY_BEAT_SCHEDULE = {
    "expire-mining-reservations-every-60-seconds": {
        "task": "mining.expire_reservations",
        "schedule": 60.0,
    },
    "reconcile-unpaid-invoices": {
        "task": "invoicing.reconcile_unpaid_invoices",
        "schedule": float(INVOICE_RECONCILIATION_INTERVAL_SECONDS),
    },
}
