"""
Rate limiting minimal basé sur le cache Django (aucune dépendance).

Compteur par (scope, IP) sur une fenêtre fixe. Avec gunicorn à N workers et le
cache local-mémoire par défaut, la limite effective est ~N× la valeur
configurée : suffisant contre le brute-force simple. Pour un plafond strict,
configurer un cache partagé (Redis / DatabaseCache).
"""
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponse


def client_ip(request) -> str:
    # Derrière le proxy Render, l'IP cliente est le premier élément de X-Forwarded-For.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def is_limited(request, scope: str, limit: int, window: int) -> bool:
    key = f"rl:{scope}:{client_ip(request)}"
    added = cache.add(key, 1, timeout=window)
    if added:
        return False
    try:
        count = cache.incr(key)
    except ValueError:  # clé expirée entre add() et incr()
        cache.set(key, 1, timeout=window)
        return False
    return count > limit


def ratelimit_post(scope: str, setting_name: str):
    """Limite uniquement les POST de la vue. `setting_name` -> settings.X = (limit, window)."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method == "POST":
                from django.conf import settings
                limit, window = getattr(settings, setting_name)
                if is_limited(request, scope, limit, window):
                    return HttpResponse(
                        "Trop de tentatives. Réessayez dans quelques minutes.",
                        status=429,
                        content_type="text/plain; charset=utf-8",
                    )
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
