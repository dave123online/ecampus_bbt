import logging

from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from payments.models import Enrollment

from django.utils.decorators import method_decorator

from .forms import SelfRegistrationForm
from .ratelimit import ratelimit_post
from .services import verify_checkout_recovery_token

logger = logging.getLogger(__name__)


@method_decorator(ratelimit_post("login", "LOGIN_RATE_LIMIT"), name="dispatch")
class PlatformLoginView(LoginView):
    """Single entry point for students (admin uses /admin/ directly)."""
    template_name = "accounts/login.html"

    def get_success_url(self):
        user = self.request.user
        if user.is_student():
            return reverse("progress:dashboard")
        return reverse("admin:index")


@ratelimit_post("register", "REGISTER_RATE_LIMIT")
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
            logger.info(
                "New self-registration: %s (enrollment %s)",
                user.email, enrollment.pk,
            )
            return redirect("payments:checkout", enrollment_id=enrollment.pk)
    else:
        form = SelfRegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


def recover_checkout(request, token):
    """
    Magic link endpoint: allows a student who lost their session
    to resume their checkout flow.
    
    URL: /accounts/recover/<token>/
    """
    data = verify_checkout_recovery_token(token)
    if data is None:
        messages.error(
            request,
            "Ce lien a expiré ou est invalide. Veuillez vous réinscrire ou nous contacter."
        )
        return redirect("accounts:register")

    enrollment_id = data.get("enrollment_id")
    email = data.get("email")

    enrollment = Enrollment.objects.filter(
        pk=enrollment_id,
        student__email__iexact=email,
        status__in=["pending", "partially_paid"],
    ).first()

    if enrollment is None:
        messages.error(
            request,
            "Aucune inscription en cours trouvée pour ce lien."
        )
        return redirect("accounts:register")

    # Restore the session marker so checkout recognizes the owner
    request.session["pending_enrollment_id"] = enrollment.pk
    logger.info(
        "Checkout recovered via magic link: enrollment %s (%s)",
        enrollment.pk, email,
    )
    return redirect("payments:checkout", enrollment_id=enrollment.pk)


@ratelimit_post("recovery", "RECOVERY_RATE_LIMIT")
def resend_recovery_email(request):
    """
    Allows a student to request a new recovery email.
    Used from the registration page when they say "I lost my payment link".
    """
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        if not email:
            messages.error(request, "Veuillez saisir votre adresse e-mail.")
            return redirect("accounts:resend_recovery")

        from .services import send_checkout_recovery_email

        enrollment = Enrollment.objects.filter(
            student__email__iexact=email,
            status__in=["pending", "partially_paid"],
        ).first()

        if enrollment:
            send_checkout_recovery_email(enrollment)
            messages.success(
                request,
                "Un lien de paiement vous a été envoyé par e-mail. Vérifiez votre boîte de réception."
            )
        else:
            # Don't reveal whether the email exists (security)
            messages.info(
                request,
                "Si une inscription en cours existe pour cet e-mail, un lien vous sera envoyé."
            )

        return redirect("accounts:login")

    return render(request, "accounts/resend_recovery.html")
