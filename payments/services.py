"""
Payment-driven access unlocking.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone

from courses.models import Notion
from progress.models import StudentProgress

from .models import Enrollment, Payment

logger = logging.getLogger(__name__)


def paid_notions_available_for(student):
    enrollment = (
        Enrollment.objects
        .filter(student=student)
        .select_related("plan")
        .first()
    )

    if enrollment is None:
        return None

    return enrollment.paid_notion_count()


def _sync_unlocked_window(enrollment):
    """
    Open the payment window on the first paid notions of the level,
    while respecting sequential progression.
    """
    available = enrollment.paid_notion_count()
    student = enrollment.student

    if available <= 0 or not student.level:
        return

    notions = list(
        Notion.objects
        .filter(level=student.level)
        .order_by("order")[:available]
    )

    previous_status = None

    for notion in notions:
        progress, _ = StudentProgress.objects.get_or_create(
            student=student,
            notion=notion,
        )

        if notion.is_first_in_level():
            if progress.status == "locked":
                progress.status = "in_progress"
                progress.unlocked_at = timezone.now()
                progress.save(update_fields=["status", "unlocked_at"])
        else:
            if previous_status == "completed" and progress.status == "locked":
                progress.status = "in_progress"
                progress.unlocked_at = timezone.now()
                progress.save(update_fields=["status", "unlocked_at"])

        previous_status = progress.status


def activate_student(enrollment):
    student = enrollment.student

    if not student.is_active:
        student.is_active = True
        student.save(update_fields=["is_active"])


def _send_payment_email(enrollment_id, payment_id):
    """
    Send payment confirmation email outside DB transaction.
    """
    try:
        enrollment = (
            Enrollment.objects
            .select_related("plan", "student")
            .get(pk=enrollment_id)
        )
        payment = Payment.objects.get(pk=payment_id)

        from accounts.services import send_payment_confirmation_email

        send_payment_confirmation_email(enrollment, payment)
    except Exception:
        logger.exception(
            "Failed to send payment confirmation email for enrollment=%s payment=%s",
            enrollment_id,
            payment_id,
        )


@db_transaction.atomic
def mark_payment_success(payment: Payment, provider_ref: str = "", raw=None):
    """
    Idempotently confirm a payment row, then recompute access.
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    enrollment = (
        Enrollment.objects
        .select_related("plan", "student")
        .get(pk=payment.enrollment_id)
    )

    if payment.status != "success":
        payment.status = "success"
        payment.verified_at = timezone.now()

        if provider_ref:
            payment.provider_ref = provider_ref

        if raw is not None:
            payment.raw_webhook = raw

        try:
            # Savepoint : une collision sur la contrainte unique provider_ref
            # (transaction déjà utilisée par un autre paiement) ne doit pas
            # casser la transaction englobante.
            with db_transaction.atomic():
                payment.save(update_fields=[
                    "status",
                    "verified_at",
                    "provider_ref",
                    "raw_webhook",
                ])
        except IntegrityError:
            logger.warning(
                "Payment %s NOT confirmed: provider_ref %r already bound to another payment",
                payment.pk, provider_ref,
            )
            return enrollment

        _touch_enrollment(enrollment.pk)

        enrollment.refresh_from_db()
        activate_student(enrollment)
        enrollment.refresh_status()
        _sync_unlocked_window(enrollment)

        logger.info(
            "Payment %s (%s) confirmed for %s — status=%s, notions=%s",
            payment.pk,
            payment.provider,
            enrollment.student.username,
            enrollment.status,
            enrollment.paid_notion_count(),
        )

        enrollment_id = enrollment.pk
        payment_id = payment.pk

        db_transaction.on_commit(
            lambda: _send_payment_email(enrollment_id, payment_id)
        )

    return enrollment


@db_transaction.atomic
def mark_payment_failed(payment: Payment, provider_ref: str = "", raw=None):
    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    if payment.status == "initiated":
        payment.status = "failed"

        if provider_ref:
            payment.provider_ref = provider_ref

        if raw is not None:
            payment.raw_webhook = raw

        payment.save(update_fields=[
            "status",
            "provider_ref",
            "raw_webhook",
        ])

    return payment.enrollment


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

def _touch_enrollment(enrollment_id):
    Enrollment.objects.filter(pk=enrollment_id).update(last_activity_at=timezone.now())


def recheck_pending_payments(max_age_minutes=30):
    from . import providers

    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)

    stale = (
        Payment.objects
        .filter(status="initiated", created_at__lt=cutoff)
        .select_related("enrollment__plan", "enrollment__student")
    )

    confirmed = failed_count = unreachable = 0

    for payment in stale:
        try:
            if payment.provider == "flutterwave":
                tx_ref = f"flw-{payment.id}-{payment.verification_token}"
                verified = providers.flutterwave_verify(tx_ref)

                if verified is not None:
                    mark_payment_success(
                        verified,
                        provider_ref=verified.provider_ref or tx_ref,
                    )
                    confirmed += 1
                else:
                    mark_payment_failed(payment)
                    failed_count += 1

            else:
                if payment.provider_ref and providers.verify_kkiapay_payment(
                    payment, payment.provider_ref
                ):
                    mark_payment_success(payment, provider_ref=payment.provider_ref)
                    confirmed += 1
                elif payment.created_at < timezone.now() - timedelta(hours=24):
                    mark_payment_failed(payment)
                    failed_count += 1
                else:
                    unreachable += 1

        except Exception:
            logger.exception(
                "recheck_pending_payments: provider call failed for payment %s",
                payment.pk,
            )
            unreachable += 1

    return {
        "checked": len(stale),
        "confirmed": confirmed,
        "failed": failed_count,
        "awaiting": unreachable,
    }


def _no_activity_since(cutoff):
    from django.db.models import Q

    return (
        Q(last_activity_at__isnull=True, created_at__lt=cutoff)
        | Q(last_activity_at__lt=cutoff)
    )


def cancel_abandoned_enrollments(days=None):
    days = days or getattr(settings, "ENROLLMENT_ABANDON_AFTER_DAYS", 7)
    cutoff = timezone.now() - timedelta(days=days)

    qs = (
        Enrollment.objects
        .filter(status="pending")
        .exclude(payments__status="success")
        .filter(_no_activity_since(cutoff))
        .select_related("student")
    )

    count = qs.count()

    for enrollment in qs:
        logger.info(
            "Cancelling abandoned enrollment %s (%s)",
            enrollment.pk,
            enrollment.student.username,
        )
        enrollment.student.delete()

    return count
