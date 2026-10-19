from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse_lazy


class PlatformLoginView(LoginView):
    """Single entry point for students (admin uses /admin/ directly)."""
    template_name = "accounts/login.html"

    def get_success_url(self):
        user = self.request.user
        if user.is_student():
            return reverse_lazy("progress:dashboard")
        return reverse_lazy("admin:index")
