"""
Django settings for the English e-learning platform.
Trimmed to the essentials relevant to this project's architecture.
"""
from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# IMPORTANT:
# Charge réellement le fichier .env.
# Sans cela, les variables définies dans .env ne sont pas utilisées.
load_dotenv(BASE_DIR / ".env")

from django.core.exceptions import ImproperlyConfigured

# Sécurité par défaut : DEBUG est OFF sauf si on l'active explicitement (.env local).
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY doit être défini (DEBUG=0).")
elif not DEBUG and SECRET_KEY.startswith("change-me"):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY ne doit pas rester à sa valeur d'exemple.")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
# Render injecte automatiquement le hostname public du service.
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if _render_host and _render_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_render_host)

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
    "dashboard_admin",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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

# Render fournit DATABASE_URL. Repli : variables DB_* (DJANGO_USE_POSTGRES=1) puis SQLite.
_database_url = os.environ.get("DATABASE_URL")
USE_POSTGRES = os.environ.get("DJANGO_USE_POSTGRES", "0") == "1"

if _database_url:
    import dj_database_url

    DATABASES = {
        # conn_health_checks : Neon met le compute en veille (scale-to-zero) ; sans ça,
        # la 1re requête après une pause peut tomber sur une connexion morte.
        "default": dj_database_url.parse(
            _database_url, conn_max_age=600, conn_health_checks=True
        ),
    }
elif USE_POSTGRES:
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
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Porto-Novo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# (dashboard_admin/static est déjà trouvé par AppDirectoriesFinder.)
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

# Médias protégés (PDF/audio). Sur Render : monter un Persistent Disk et
# pointer MEDIA_ROOT dessus (ex. /var/data/media) — voir render.yaml.
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "progress:dashboard"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Fixed platform-wide quiz pass threshold
QUIZ_PASS_THRESHOLD = 70

# ---------------------------------------------------------------------------
# File serving strategy (production)
# ---------------------------------------------------------------------------
USE_X_ACCEL_REDIRECT = os.environ.get("USE_X_ACCEL_REDIRECT", "0") == "1"
USE_X_SENDFILE = os.environ.get("USE_X_SENDFILE", "0") == "1"

# ---------------------------------------------------------------------------
# Email configuration
# ---------------------------------------------------------------------------
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@ecampus-bbt.com")

# ---------------------------------------------------------------------------
# Self-service registration / payments
# ---------------------------------------------------------------------------
FLUTTERWAVE_SECRET_KEY = os.environ.get("FLUTTERWAVE_SECRET_KEY", "")
FLUTTERWAVE_PUBLIC_KEY = os.environ.get("FLUTTERWAVE_PUBLIC_KEY", "")
FLUTTERWAVE_BASE_URL = os.environ.get("FLUTTERWAVE_BASE_URL", "https://api.flutterwave.com/v3")
FLUTTERWAVE_SECRET_HASH = os.environ.get("FLUTTERWAVE_SECRET_HASH", "")

# KkiaPay — 3 clés (dashboard → Développeurs → Clés d'API) :
#   public  = widget JS (côté navigateur)
#   private + secret = API serveur de vérification des transactions
KKIAPAY_PUBLIC_KEY = os.environ.get("KKIAPAY_PUBLIC_KEY", "")
KKIAPAY_PRIVATE_KEY = os.environ.get("KKIAPAY_PRIVATE_KEY", "")
KKIAPAY_SECRET_KEY = os.environ.get("KKIAPAY_SECRET_KEY", "")
# "Hash secret" saisi dans le dashboard KkiaPay (Webhooks) : renvoyé dans l'en-tête x-kkiapay-secret.
KKIAPAY_WEBHOOK_SECRET = os.environ.get("KKIAPAY_WEBHOOK_SECRET", "")
# 1 = mode test (widget + API sandbox, numéros de test KkiaPay).
KKIAPAY_SANDBOX = os.environ.get("KKIAPAY_SANDBOX", "0") == "1"
KKIAPAY_BASE_URL = os.environ.get(
    "KKIAPAY_BASE_URL",
    "https://api-sandbox.kkiapay.me" if KKIAPAY_SANDBOX else "https://api.kkiapay.me",
).rstrip("/")

PLATFORM_BASE_URL = (
    os.environ.get("PLATFORM_BASE_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or "http://127.0.0.1:8000"
).rstrip("/")

FIRST_TRANCHE_RATIO = float(os.environ.get("FIRST_TRANCHE_RATIO", "0.34"))
ENROLLMENT_ABANDON_AFTER_DAYS = int(os.environ.get("ENROLLMENT_ABANDON_AFTER_DAYS", "7"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Sur Render le FS est éphémère et les logs sont déjà collectés depuis stdout :
# la console suffit. LOG_TO_FILE=1 réactive le fichier tournant (VPS classique).
LOG_TO_FILE = os.environ.get("LOG_TO_FILE", "0") == "1"
LOG_DIR = BASE_DIR / "logs"
if LOG_TO_FILE:
    os.makedirs(LOG_DIR, exist_ok=True)

_LOG_HANDLERS = ["console", "file"] if LOG_TO_FILE else ["console"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {module}.{funcName}:{lineno} — {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": _LOG_HANDLERS,
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": _LOG_HANDLERS,
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": _LOG_HANDLERS,
            "level": "ERROR",
            "propagate": False,
        },
        "payments": {
            "handlers": _LOG_HANDLERS,
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "accounts": {
            "handlers": _LOG_HANDLERS,
            "level": "INFO",
            "propagate": False,
        },
    },
}

if LOG_TO_FILE:
    LOGGING["handlers"]["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(LOG_DIR / "ecampus.log"),
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "formatter": "verbose",
    }

# ---------------------------------------------------------------------------
# Rate limiting (voir accounts/ratelimit.py) — compteurs en cache local.
# ---------------------------------------------------------------------------
LOGIN_RATE_LIMIT = (10, 300)       # 10 POST / 5 min / IP
REGISTER_RATE_LIMIT = (8, 3600)    # 8 POST / heure / IP
RECOVERY_RATE_LIMIT = (5, 3600)    # 5 POST / heure / IP

# ---------------------------------------------------------------------------
# Sécurité production (Render termine le TLS devant gunicorn)
# ---------------------------------------------------------------------------
_csrf_origins = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
if PLATFORM_BASE_URL.startswith("https://") and PLATFORM_BASE_URL not in _csrf_origins:
    _csrf_origins.append(PLATFORM_BASE_URL)
CSRF_TRUSTED_ORIGINS = _csrf_origins

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "1") == "1"
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"
