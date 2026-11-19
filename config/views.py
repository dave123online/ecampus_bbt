from django.http import HttpResponse
from django.shortcuts import redirect, render


def home_redirect(request):
    if not request.user.is_authenticated:
        return render(request, "home.html")
    if request.user.is_student():
        return redirect("progress:dashboard")
    return redirect("admin:index")


def healthz(request):
    """Health check Render (exempté de la redirection HTTPS, sans accès DB)."""
    return HttpResponse("ok", content_type="text/plain")
