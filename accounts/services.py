"""
Services for account recovery and transactional emails.
"""
import logging

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)

MAGIC_LINK_MAX_AGE = 3600 * 24  # 24 hours


def generate_checkout_recovery_token(enrollment_id: int, student_email: str) -> str:
    """
    Generate a signed token that allows resuming checkout for a pending enrollment.
    """
    return signing.dumps(
        {"enrollment_id": enrollment_id, "email": student_email},
        salt="checkout-recovery",
    )


def verify_checkout_recovery_token(token: str) -> dict | None:
    """
    Verify and decode a checkout recovery token.
    """
    try:
        return signing.loads(
            token,
            salt="checkout-recovery",
            max_age=MAGIC_LINK_MAX_AGE,
        )
    except (signing.BadSignature, signing.SignatureExpired):
        return None


def send_checkout_recovery_email(enrollment) -> bool:
    """
    Send a magic link to the student so they can resume checkout.
    """
    student = enrollment.student

    if not student.email:
        logger.warning(
            "Cannot send recovery email: student %s has no email",
            student.pk,
        )
        return False

    token = generate_checkout_recovery_token(enrollment.pk, student.email)
    recovery_url = settings.PLATFORM_BASE_URL.rstrip("/") + reverse(
        "accounts:recover_checkout",
        args=[token],
    )

    subject = "eCampus BBT — Finalisez votre inscription"
    message = f"""Bonjour {student.first_name or student.username},

Vous avez commencé une inscription sur eCampus BBT mais n'avez pas finalisé votre paiement.

Cliquez sur le lien ci-dessous pour reprendre votre paiement là où vous l'avez laissé :

{recovery_url}

Ce lien est valable 24 heures.

À très bientôt,
L'équipe eCampus BBT
"""

    html_message = render_to_string("emails/checkout_recovery.html", {
        "student": student,
        "recovery_url": recovery_url,
        "plan": enrollment.plan,
    })

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[student.email],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(
            "Checkout recovery email sent to %s (enrollment %s)",
            student.email,
            enrollment.pk,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to send recovery email to %s (enrollment %s)",
            student.email,
            enrollment.pk,
        )
        return False


def send_payment_confirmation_email(enrollment, payment) -> bool:
    """
    Send a confirmation email after a successful payment.
    """
    student = enrollment.student

    if not student.email:
        return False

    subject = "eCampus BBT — Paiement confirmé ✅"

    message = f"""Bonjour {student.first_name or student.username},

Votre paiement de {payment.amount} {payment.currency} a été confirmé.

Formule : {enrollment.plan.name}
Notions débloquées : {enrollment.paid_notion_count()} / {enrollment.plan.notion_count}

Vous pouvez accéder à vos leçons dès maintenant sur eCampus BBT.

Merci pour votre confiance !
L'équipe eCampus BBT
"""

    html_message = render_to_string("emails/payment_confirmation.html", {
        "student": student,
        "payment": payment,
        "enrollment": enrollment,
        "PLATFORM_BASE_URL": settings.PLATFORM_BASE_URL.rstrip("/"),
    })

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[student.email],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(
            "Payment confirmation email sent to %s (payment %s)",
            student.email,
            payment.pk,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to send payment confirmation email to %s",
            student.email,
        )
        return False


def send_quiz_completion_email(student, notion, score) -> bool:
    """
    Send a congratulation email when a student completes a notion quiz.
    """
    if not student.email:
        return False

    next_notion = notion.next()

    subject = f"eCampus BBT — Notion validée : {notion.title} 🎉"

    message = f"""Bonjour {student.first_name or student.username},

Félicitations ! Vous avez validé la notion « {notion.title} » avec un score de {score}%.

"""

    if next_notion:
        message += f"La notion suivante « {next_notion.title} » est maintenant débloquée.\n"
    else:
        message += "Vous avez terminé toutes les notions de ce niveau. Bravo !\n"

    message += """
Continuez sur votre lancée !
L'équipe eCampus BBT
"""

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[student.email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Failed to send quiz completion email to %s", student.email)
        return False
