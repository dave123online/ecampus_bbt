"""
Payment-driven access unlocking.

Entry points: `mark_payment_success()` / `mark_payment_failed()` — called
ONLY from verified code paths (server-side verify on callback, or
authenticated webhook). They are idempotent and safe to call multiple
times for the same transaction.

Unlocking model for self-service (paid) students:
- The first `paid_notion_count()` notions of their level are *available*
  (unlocked), but they still must follow the sequential path: notion N+1
  only opens once notion N is completed (existing progress.services logic).
- Admin-created students are unaffected (no Enrollment => unlimited).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import Max
from django.utils import timezone

from courses.models import Notion
from progress.models import StudentProgress

from .models import Enrollment, Payment

logger = logging.getLogger(__name__)


def paid_notions_available_for(student):
    """How many notions of the student's level are validly paid for.
    Returns None for admin-created students (= unlimited)."""
    enrollment = Enrollment.objects.filter(student=student).select_related("plan").first()
    if enrollment is None:
        return None
    return enrollment.paid_notion_count()


def _sync_unlocked_window(enrollment):
    """Open the payment window on the first `paid` notions of the level."""
    available = enrollment.paid_notion_count()
    notions = list(
        Notion.objects.filter(level=enrollment.student.level).order_by("order")[:available]
    )
    for notion in notions:
        progress, _ = StudentProgress.objects.get_or_create(
            student=enrollment.student, notion=notion
        )
        if progress.status == "locked":
            progress.status = "in_progress"
            progress.unlocked_at = timezone.now()
            progress.save(update_fields=["status", "unlocked_at"])


def activate_student(enrollment):
    """First verified payment: the account becomes usable."""
    student = enrollment.student
    if not student.is_active:
        student.is_active = True
        student.save(update_fields=["is_active"])


@db_transaction.atomic
def mark_payment_success(payment: Payment, provider_ref: str = "", raw=None):
    """Idempotently confirm a payment row, then recompute access."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status != "success":
        payment.status = "success"
        payment.verified_at = timezone.now()
        if provider_ref:
            payment.provider_ref = provider_ref
        if raw is not None:
            payment.raw_webhook = raw
        payment.save(update_fields=["status", "verified_at", "provider_ref", "raw_webhook"])
        _touch_enrollment(payment.enrollment_id)

    enrollment = Enrollment.objects.select_related("plan", "student").get(pk=payment.enrollment_id)
    activate_student(enrollment)
    # Re-derive status from the DB row (refresh_from_db avoids stale totals).
    enrollment.refresh_status()
    _sync_unlocked_window(enrollment)
    logger.info(
        "Payment %s (%s) confirmed for %s — status=%s, notions=%s",
        payment.pk, payment.provider, enrollment.student.username,
        enrollment.status, enrollment.paid_notion_count(),
    )
    return enrollment


@db_transaction.atomic
def mark_payment_failed(payment: Payment, provider_ref: str = "", raw=None):
    """A failed/cancelled tranche frees its slot so the student can retry."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == "initiated":
        payment.status = "failed"
        if provider_ref:
            payment.provider_ref = provider_ref
        if raw is not None:
            payment.raw_webhook = raw
        payment.save(update_fields=["status", "provider_ref", "raw_webhook"])
    return payment.enrollment


# ---------------------------------------------------------------------------
# Housekeeping — keeps the system autonomous (no manual intervention needed)
# ---------------------------------------------------------------------------

def _touch_enrollment(enrollment_id):
    Enrollment.objects.filter(pk=enrollment_id).update(last_activity_at=timezone.now())


def recheck_pending_payments(max_age_minutes=30):
    """Safety net for LOST WEBHOKS / self-hosted callbacks behind a firewall.

    Re-queries each provider's verify API for tranches stuck in `initiated`
    for more than `max_age_minutes`. Confirms genuine payments, fails expired
    ones so the installment slot frees up for a retry. Returns a summary dict.

    Designed to be called from a management command (cron / systemd timer).
    """
    from . import providers

    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    stale = (Payment.objects
             .filter(status="initiated", created_at__lt=cutoff)
             .select_related("enrollment__plan", "enrollment__student"))
    confirmed = failed_count = unreachable = 0

    for payment in stale:
        try:
            if payment.provider == "flutterwave":
                # txRef format: flw-<id>-<token>
                verified = providers.flutterwave_verify(f"flw-{payment.id}-{payment.verification_token}")
                if verified is not None:
                    mark_payment_success(verified, provider_ref=verified.provider_ref or "")
                    confirmed += 1
                else:
                    mark_payment_failed(payment)
                    failed_count += 1
            else:  # kkiapay — no server-side lookup without a confirmation id;
                # nothing verifiable yet: expire very old tranches (>24h) only.
                if payment.created_at < timezone.now() - timedelta(hours=24):
                    mark_payment_failed(payment)
                    failed_count += 1
                else:
                    unreachable += 1
        except Exception:
            logger.exception("recheck_pending_payments: provider call failed for payment %s",
                             payment.pk)
            unreachable += 1

    return {"checked": len(stale), "confirmed": confirmed,
            "failed": failed_count, "awaiting": unreachable}


def _no_activity_since(cutoff):
    """No payment activity since `cutoff`: either never initiated a tranche
    (created_at older) or last_activity_at older."""
    from django.db.models import Q
    return Q(last_activity_at__isnull=True, created_at__lt=cutoff) | Q(last_activity_at__lt=cutoff)


def cancel_abandoned_enrollments(days=None):
    """Auto-cancel signups that never paid anything and went silent.

    Targets enrollments with status `pending`, zero successful cents paid,
    and no activity for `days` days (default settings.ENROLLMENT_ABANDON_AFTER_DAYS).
    The inactive student account is removed with it (CASCADE), so the e-mail
    becomes free again for a genuine signup. Never touches partially/fully
    paid enrollments.
    """
    days = days or getattr(settings, "ENROLLMENT_ABANDON_AFTER_DAYS", 7)
    cutoff = timezone.now() - timedelta(days=days)
    qs = (Enrollment.objects
          .filter(status="pending")
          .exclude(payments__status="success")
          .filter(_no_activity_since(cutoff)))
    count = qs.count()
    for enrollment in qs:
        logger.info("Cancelling abandoned enrollment %s (%s)",
                    enrollment.pk, enrollment.student.username)
        enrollment.student.delete()  # CASCADE removes enrollment + payments
    return count
