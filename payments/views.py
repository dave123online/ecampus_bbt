import json
import logging

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import providers
from .models import Enrollment, Payment
from .services import (
    _touch_enrollment, mark_payment_failed, mark_payment_success,
)

logger = logging.getLogger(__name__)


def check_payment_status(request, payment_id):
    """Server-side status poll for the success page.

    The browser cannot confirm a payment — only our API verification can.
    This endpoint re-checks a still-`initiated` tranche with the provider
    and returns JSON so the success page can flip to 'confirmed' without
    any manual intervention (covers lost/delayed webhooks).
    """
    payment = Payment.objects.filter(pk=payment_id).select_related("enrollment__student").first()
    if payment is None or not _checkout_owner_ok(request, payment.enrollment):
        return _json_response({"detail": "not found"}, 404)

    if payment.status == "success":
        return _json_response({"status": "success"})

    # Try to advance it now via server-side verification.
    if payment.provider == "flutterwave":
        tx_ref = f"flw-{payment.id}-{payment.verification_token}"
        verified = providers.flutterwave_verify(tx_ref)
        if verified is not None:
            mark_payment_success(verified, provider_ref=tx_ref)
            return _json_response({"status": "success"})
    else:  # kkiapay: lookup by confirmation id if we already have one
        verified_id = providers.kkiapay_lookup_by_token(payment)
        if verified_id and providers.verify_kkiapay_payment(payment, verified_id):
            mark_payment_success(payment, provider_ref=verified_id)
            return _json_response({"status": "success"})

    fresh = Payment.objects.get(pk=payment.pk)
    return _json_response({"status": fresh.status})


def _checkout_owner_ok(request, enrollment) -> bool:
    """Checkout is reachable by the freshly signed-up (inactive) student via
    a session marker, or by an authenticated owner (repeat tranches)."""
    if request.user.is_authenticated and request.user.pk == enrollment.student_id:
        return True
    return request.session.get("pending_enrollment_id") == enrollment.pk


def checkout(request, enrollment_id):
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("plan", "student"), pk=enrollment_id
    )
    if not _checkout_owner_ok(request, enrollment):
        return redirect("accounts:login")

    if enrollment.status == "paid":
        return render(request, "payments/checkout.html", {
            "enrollment": enrollment, "already_paid": True,
        })

    tranche_index, amount = providers.next_tranche_state(enrollment)
    error = None

    if request.method == "POST" and tranche_index is not None:
        provider = request.POST.get("provider")
        if provider not in ("kkiapay", "flutterwave"):
            error = "Moyen de paiement inconnu."
        else:
            # Amount is ALWAYS recomputed server-side — POST data never decides it.
            try:
                payment = Payment.objects.create(
                    enrollment=enrollment, provider=provider,
                    amount=amount, currency=enrollment.plan.currency,
                    tranche_index=tranche_index,
                )
            except IntegrityError:
                payment = None  # a live tranche already exists for this slot
                error = "Une transaction est déjà en cours pour cette tranche."
            if payment is not None:
                _touch_enrollment(enrollment.pk)  # keeps abandon-timer accurate
                return _start_provider_payment(request, payment, enrollment)

    return render(request, "payments/checkout.html", {
        "enrollment": enrollment,
        "tranche_index": tranche_index,
        "amount": amount,
        "tranches_total": enrollment.plan.max_installments,
        "error": error,
        "flutterwave_public_key": settings.FLUTTERWAVE_PUBLIC_KEY,
    })


def _start_provider_payment(request, payment, enrollment):
    student = enrollment.student
    email = student.email or f"{student.username}@ecampus.local"
    name = student.first_name or student.username
    phone = student.phone_number

    if payment.provider == "flutterwave":
        try:
            link = providers.flutterwave_initiate_payment(payment, email, name, phone)
        except Exception:
            logger.exception("Flutterwave initiate failed")
            mark_payment_failed(payment)
            messages.error(request, "Le paiement par carte est momentanément indisponible.")
            return redirect("payments:checkout", enrollment_id=enrollment.pk)
        return redirect(link)

    # KkiaPay: auto-submitted inline form toward their hosted page.
    post_url, fields = providers.kkiapay_initiate_payment(payment, phone)
    return render(request, "payments/kkiapay_redirect.html",
                  {"post_url": post_url, "fields": fields})


# ---------------------------------------------------------------------------
# Flutterwave: browser callback + server-to-server webhook
# ---------------------------------------------------------------------------

def flutterwave_callback(request, payment_id):
    """Redirect target after FLW checkout. NOT trusted alone: we re-verify
    the transaction server-side via the API before granting anything."""
    tx_ref = request.GET.get("tx_ref", "")
    payment = Payment.objects.filter(pk=payment_id).select_related("enrollment").first()
    if payment is None:
        return redirect("home")

    verified = providers.flutterwave_verify(tx_ref) if tx_ref else None
    if verified is not None and verified.pk == payment.pk:
        enrollment = mark_payment_success(payment, provider_ref=tx_ref)
        _mark_session_done(request, enrollment)
        return redirect("payments:success", enrollment_id=enrollment.pk)

    mark_payment_failed(payment)
    enrollment = payment.enrollment
    _mark_session_done(request, enrollment)
    messages.warning(request, "Paiement non confirmé. Réessayez ou choisissez un autre moyen.")
    return redirect("payments:checkout", enrollment_id=enrollment.pk)


@csrf_exempt
@require_POST
def flutterwave_webhook(request):
    """Server-to-server event. Double-checked: verif-hash header AND API
    verification of the transaction. Idempotent via mark_payment_success."""
    if not providers.flutterwave_webhook_is_authentic(request):
        return _json_response({"detail": "invalid signature"}, 403)

    try:
        payload = json.loads(request.body.decode())
    except (ValueError, UnicodeDecodeError):
        return _json_response({"detail": "bad payload"}, 400)

    data = payload.get("data") or {}
    if data.get("event") != "charge.completed" or data.get("status") != "successful":
        return _json_response({"detail": "ignored"})

    tx_ref = data.get("tx_ref", "")
    verified = providers.flutterwave_verify(tx_ref)
    if verified is not None:
        mark_payment_success(verified, provider_ref=tx_ref, raw=payload)
    return _json_response({"detail": "ok"})


# ---------------------------------------------------------------------------
# KkiaPay: data_return callback + webhook
# ---------------------------------------------------------------------------

def kkiapay_callback(request, payment_id):
    """KkiaPay returns here with `data` (echoed token) and a confirmation id.
    We verify the confirmation id server-side before granting anything.

    Mode `no-redirect=true`: KkiaPay sends this as a **POST** form-encoded
    request (id + data), which is more robust than a GET redirect. We still
    accept GET for accounts configured in redirect mode.
    """
    payment = Payment.objects.filter(pk=payment_id).select_related("enrollment").first()
    if payment is None:
        return redirect("home")

    source = request.POST if request.method == "POST" else request.GET
    returned_data = source.get("data", "")
    confirmation_id = (source.get("id") or source.get("transaction_id")
                       or source.get("rcode") or "")

    if returned_data != payment.verification_token:
        mark_payment_failed(payment)
        enrollment = payment.enrollment
        _mark_session_done(request, enrollment)
        messages.warning(request, "Réponse de paiement invalide. Réessayez.")
        return redirect("payments:checkout", enrollment_id=enrollment.pk)

    if providers.verify_kkiapay_payment(payment, confirmation_id):
        enrollment = mark_payment_success(payment, provider_ref=confirmation_id,
                                          raw=dict(source.items()))
        _mark_session_done(request, enrollment)
        return redirect("payments:success", enrollment_id=enrollment.pk)

    # Not confirmed yet — keep the tranche 'initiated' so the async webhook
    # can still confirm it; show pending state to the user meanwhile.
    enrollment = payment.enrollment
    request.session["pending_payment_id"] = payment.pk  # lets success page poll
    _mark_session_done(request, enrollment)
    messages.info(request, "Paiement en cours de confirmation…")
    return redirect("payments:success", enrollment_id=enrollment.pk)


@csrf_exempt
@require_POST
def kkiapay_webhook(request, payment_id=None):
    """Async KkiaPay notification. Verify token match + server-side check."""
    payload = None
    if request.POST:
        payload = dict(request.POST.items())
    else:
        try:
            payload = json.loads(request.body.decode())
        except (ValueError, UnicodeDecodeError):
            pass
    if not payload:
        return _json_response({"detail": "bad payload"}, 400)

    token = payload.get("data", "")
    payment = Payment.objects.filter(verification_token=token).select_related("enrollment").first()
    if payment is None:
        return _json_response({"detail": "unknown transaction"}, 404)

    status = str(payload.get("status", "")).lower()
    confirmation_id = str(payload.get("id", "") or payload.get("transaction_id", ""))

    if status in ("failed", "cancelled", "canceled", "expired"):
        mark_payment_failed(payment, provider_ref=confirmation_id, raw=payload)
        return _json_response({"detail": "failed recorded"})

    if providers.verify_kkiapay_payment(payment, confirmation_id):
        mark_payment_success(payment, provider_ref=confirmation_id, raw=payload)
        return _json_response({"detail": "ok"})
    return _json_response({"detail": "unverified"}, 202)


def success(request, enrollment_id):
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("plan", "student"), pk=enrollment_id
    )
    if not _checkout_owner_ok(request, enrollment):
        return redirect("accounts:login")
    # The most recent tranche drives the polling widget on the page.
    latest_payment = (enrollment.payments
                      .exclude(status="failed")
                      .order_by("-created_at").first())
    return render(request, "payments/success.html", {
        "enrollment": enrollment,
        "latest_payment": latest_payment,
    })


def _mark_session_done(request, enrollment):
    request.session.pop("pending_enrollment_id", None)


def _json_response(data, status=200):
    from django.http import JsonResponse
    return JsonResponse(data, status=status)
