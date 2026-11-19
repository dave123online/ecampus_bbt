"""Décorateurs pour sécuriser les vues d'administration."""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def admin_required(view_func):
    """
    Restreint l'accès aux utilisateurs ayant le rôle 'admin'
    (ou is_staff=True pour compatibilité avec l'admin Django).
    """
    @wraps(view_func)
    @login_required(login_url="accounts:login")
    def _wrapped(request, *args, **kwargs):
        user = request.user
        is_admin = (
            getattr(user, "role", None) == "admin"
            or user.is_staff
            or user.is_superuser
        )
        if not is_admin:
            raise PermissionDenied("Accès réservé aux administrateurs.")
        return view_func(request, *args, **kwargs)
    return _wrapped
