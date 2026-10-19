"""
Django settings for the English e-learning platform.
Trimmed to the essentials relevant to this project's architecture.
"""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "change-me-in-.env")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

AUTH_USER_MODEL = "accounts.CustomUser"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Project apps
    "accounts",
    "courses",
    "quizzes",
    "progress",
    "payments",
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

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

USE_POSTGRES = os.environ.get("DJANGO_USE_POSTGRES", "0") == "1"

if USE_POSTGRES:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "english_platform"),
            "USER": os.environ.get("DB_USER", "postgres"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }
else:
    # Local dev default — no Postgres install required.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files are served ONLY through authenticated views (courses.views),
# never via a public MEDIA_URL. MEDIA_ROOT is kept outside any publicly
# served path — see courses/views.py for the protected file serving.
MEDIA_ROOT = BASE_DIR / "media"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "progress:dashboard"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Fixed platform-wide quiz pass threshold (see quizzes/models.py)
QUIZ_PASS_THRESHOLD = 70

# ---------------------------------------------------------------------------
# Self-service registration / payments
# ---------------------------------------------------------------------------
PAYMENT_SECRET_KEY = os.environ.get("PAYMENT_SECRET_KEY", SECRET_KEY)

FLUTTERWAVE_SECRET_KEY = os.environ.get("FLUTTERWAVE_SECRET_KEY", "")
FLUTTERWAVE_PUBLIC_KEY = os.environ.get("FLUTTERWAVE_PUBLIC_KEY", "")
FLUTTERWAVE_BASE_URL = os.environ.get("FLUTTERWAVE_BASE_URL", "https://api.flutterwave.com/v3")
# Hash statique configuré dans le dashboard FLW pour signer les webhooks.
FLUTTERWAVE_SECRET_HASH = os.environ.get("FLUTTERWAVE_SECRET_HASH", "")

KKIAPAY_SECRET_KEY = os.environ.get("KKIAPAY_SECRET_KEY", "")
KKIAPAY_PUBLIC_KEY = os.environ.get("KKIAPAY_PUBLIC_KEY", "")
KKIAPAY_BASE_URL = os.environ.get("KKIAPAY_BASE_URL", "https://api.kkiapay.me/api")

# Absolu (sans /), utilisé côté serveur pour construire les URL de callback/webhook.
PLATFORM_BASE_URL = os.environ.get("PLATFORM_BASE_URL", "http://127.0.0.1:8000")

# Part du prix à payer avant le premier déverrouillage de notions (tranche 1).
FIRST_TRANCHE_RATIO = 0.34

# Au-delà de ce nombre de jours sans aucune activité, une inscription
# auto-created jamais payée est annulée automatiquement
# (manage.py payments_housekeeping — à planifier en cron).
ENROLLMENT_ABANDON_AFTER_DAYS = int(os.environ.get("ENROLLMENT_ABANDON_AFTER_DAYS", "7"))
