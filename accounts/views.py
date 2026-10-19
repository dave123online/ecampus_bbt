from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import SelfRegistrationForm


class PlatformLoginView(LoginView):
    """Single entry point for students (admin uses /admin/ directly)."""
    template_name = "accounts/login.html"

    def get_success_url(self):
        user = self.request.user
        if user.is_student():
            return reverse("progress:dashboard")
        return reverse("admin:index")


def register(request):
    """Public self-registration: creates a pending account + Enrollment,
    then sends the student straight to checkout. No manual validation."""
    if request.user.is_authenticated:
        return redirect("progress:dashboard")

    if request.method == "POST":
        form = SelfRegistrationForm(request.POST)
        if form.is_valid():
            user, enrollment = form.create_user_and_enrollment()
            # Remember the fresh signup so checkout can authenticate its
            # owner without forcing login on an is_active=False account.
            request.session["pending_enrollment_id"] = enrollment.pk
            return redirect("payments:checkout", enrollment_id=enrollment.pk)
    else:
        form = SelfRegistrationForm()
    return render(request, "accounts/register.html", {"form": form})
