import json
import logging
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import providers
from .models import Enrollment, Payment
from .services import (
    _touch_enrollment,
    mark_payment_failed,
    mark_payment_success,
)

logger = logging.getLogger(__name__)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _json_response(data, status=200):
    from django.http import JsonResponse
    return JsonResponse(data, status=status)


def _checkout_owner_ok(request, enrollment) -> bool:
    if request.user.is_authenticated and request.user.pk == enrollment.student_id:
        return True
    return request.session.get("pending_enrollment_id") == enrollment.pk


def _session_owns_enrollment(request, enrollment) -> bool:
    """Preuve de propriété : le marqueur posé en session à l'inscription / via le lien magique."""
    return request.session.get("pending_enrollment_id") == enrollment.pk


def _login_if_active(request, enrollment):
    # SÉCURITÉ : ne JAMAIS connecter quelqu'un sur la seule base d'un id de
    # paiement dans l'URL (ids séquentiels => prise de contrôle de compte).
    if not _session_owns_enrollment(request, enrollment):
        return False
    student = enrollment.student
    if student.is_active and not request.user.is_authenticated:
        login(
            request,
            student,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        return True
    return False


def _finalize_owner_session(request, enrollment):
    if _login_if_active(request, enrollment):
        request.session.pop("pending_enrollment_id", None)
        request.session.pop("pending_payment_id", None)
        return
    if request.user.is_authenticated and request.user.pk == enrollment.student_id:
        request.session.pop("pending_enrollment_id", None)
        request.session.pop("pending_payment_id", None)


def check_payment_status(request, payment_id):
    payment = (
        Payment.objects
        .filter(pk=payment_id)
        .select_related("enrollment__student")
        .first()
    )
    if payment is None or not _checkout_owner_ok(request, payment.enrollment):
        return _json_response({"detail": "not found"}, 404)

    if payment.status == "success":
        return _json_response({"status": "success"})

    if payment.provider == "flutterwave":
        tx_ref = f"flw-{payment.id}-{payment.verification_token}"
        verified = providers.flutterwave_verify(tx_ref)
        if verified is not None:
            mark_payment_success(verified, provider_ref=tx_ref)
            return _json_response({"status": "success"})
    elif payment.provider_ref:
        # KkiaPay : on ne peut re-vérifier que si l'id de transaction est connu
        # (reçu par le callback ou le webhook).
        if providers.verify_kkiapay_payment(payment, payment.provider_ref):
            mark_payment_success(payment, provider_ref=payment.provider_ref)
            return _json_response({"status": "success"})

    fresh = Payment.objects.get(pk=payment.pk)
    return _json_response({"status": fresh.status})


def checkout(request, enrollment_id):
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("plan", "student"),
        pk=enrollment_id,
    )
    if not _checkout_owner_ok(request, enrollment):
        messages.info(
            request,
            "Session expirée. Utilisez le lien reçu par e-mail ou réinscrivez-vous.",
        )
        return redirect("accounts:resend_recovery")

    if enrollment.status == "paid":
        return render(request, "payments/checkout.html", {
            "enrollment": enrollment, "already_paid": True,
        })

    tranche_index, amount = providers.next_tranche_state(enrollment)
    error = None

    if request.method == "POST" and tranche_index is not None:
        provider = request.POST.get("provider")

        # --- Option : paiement intégral ---
        if provider == "full" and enrollment.total_paid == 0:
            amount = enrollment.plan.price
            tranche_index = 1
            provider = request.POST.get("full_provider", "kkiapay")
            if provider not in ("kkiapay", "flutterwave"):
                provider = "kkiapay"
        elif provider not in ("kkiapay", "flutterwave"):
            error = "Moyen de paiement inconnu."
            provider = None

        if provider and not error:
            # Réutiliser un paiement initié existant pour cette tranche
            payment = (
                enrollment.payments
                .filter(status="initiated", tranche_index=tranche_index)
                .order_by("-created_at")
                .first()
            )
            if payment and (payment.provider != provider or payment.amount != amount):
                mark_payment_failed(payment)
                payment = None

            if payment is None:
                try:
                    payment = Payment.objects.create(
                        enrollment=enrollment,
                        provider=provider,
                        amount=amount,
                        currency=enrollment.plan.currency,
                        tranche_index=tranche_index,
                    )
                except IntegrityError:
                    payment = (
                        enrollment.payments
                        .filter(status="initiated", tranche_index=tranche_index)
                        .first()
                    )
                    if payment is None:
                        error = "Une transaction est déjà en cours pour cette tranche."

            if payment is not None:
                _touch_enrollment(enrollment.pk)
                return _start_provider_payment(request, payment, enrollment)

    # Option paiement intégral : proposée seulement si rien n'a été payé
    # et que le plan autorise le fractionnement.
    can_pay_full = (
        enrollment.plan.max_installments > 1
        and enrollment.total_paid == 0
        and enrollment.status == "pending"
    )

    return render(request, "payments/checkout.html", {
        "enrollment": enrollment,
        "tranche_index": tranche_index,
        "amount": amount,
        "tranches_total": enrollment.plan.max_installments,
        "error": error,
        "flutterwave_public_key": settings.FLUTTERWAVE_PUBLIC_KEY,
        "can_pay_full": can_pay_full,
        "full_price": enrollment.plan.price,
    })


def _start_provider_payment(request, payment, enrollment):
    student = enrollment.student
    email = student.email or f"{student.username}@ecampus.local"
    name = student.first_name or student.username
    phone = student.phone_number
    
    if payment.provider == "flutterwave":
        try:
            link = providers.flutterwave_initiate_payment(payment, email, name, phone)
        except RuntimeError as e:
            logger.error("Flutterwave initiate error: %s", e)
            mark_payment_failed(payment)
            messages.error(request, str(e))
            return redirect("payments:checkout", enrollment_id=enrollment.pk)
        except Exception:
            logger.exception("Flutterwave initiate failed (unexpected)")
            mark_payment_failed(payment)
            messages.error(request, "Le paiement par carte est momentanément indisponible.")
            return redirect("payments:checkout", enrollment_id=enrollment.pk)
        return redirect(link)
    
    # KkiaPay : le paiement se fait dans le widget JS officiel (page dédiée).
    return redirect("payments:kkiapay_pay", payment_id=payment.pk)


def kkiapay_pay(request, payment_id):
    """Page qui ouvre le widget KkiaPay (clé publique) pour un paiement initié."""
    payment = (
        Payment.objects
        .filter(pk=payment_id, provider="kkiapay")
        .select_related("enrollment__student", "enrollment__plan")
        .first()
    )
    if payment is None or not _checkout_owner_ok(request, payment.enrollment):
        return redirect("accounts:resend_recovery")
    if payment.status == "success":
        return redirect("payments:success", enrollment_id=payment.enrollment_id)
    if payment.status != "initiated":
        return redirect("payments:checkout", enrollment_id=payment.enrollment_id)
    if not settings.KKIAPAY_PUBLIC_KEY:
        logger.error("KKIAPAY_PUBLIC_KEY manquante : widget KkiaPay indisponible")
        mark_payment_failed(payment)
        messages.error(request, "Le paiement Mobile Money est momentanément indisponible.")
        return redirect("payments:checkout", enrollment_id=payment.enrollment_id)

    student = payment.enrollment.student
    return render(request, "payments/kkiapay_widget.html", {
        "payment": payment,
        "enrollment": payment.enrollment,
        "widget": providers.kkiapay_widget_config(payment, student),
        "checkout_url": reverse("payments:checkout", args=[payment.enrollment_id]),
    })


# ---------------------------------------------------------------------------
# Flutterwave: browser callback + server-to-server webhook
# ---------------------------------------------------------------------------

@csrf_exempt
def flutterwave_callback(request, payment_id):
    payment = (
        Payment.objects
        .filter(pk=payment_id)
        .select_related("enrollment__student")
        .first()
    )
    if payment is None:
        return redirect("home")

    enrollment = payment.enrollment
    if payment.status == "success":
        _finalize_owner_session(request, enrollment)
        return redirect("payments:success", enrollment_id=enrollment.pk)

    tx_ref = request.GET.get("tx_ref", "")
    transaction_id = request.GET.get("transaction_id") or request.GET.get("id") or ""

    verified = None
    if tx_ref:
        verified = providers.flutterwave_verify(tx_ref, transaction_id=transaction_id)

    if verified is not None and verified.pk == payment.pk:
        enrollment = mark_payment_success(
            payment,
            provider_ref=str(transaction_id or tx_ref),
        )
        _finalize_owner_session(request, enrollment)
        return redirect("payments:success", enrollment_id=enrollment.pk)

    # Un visiteur sans preuve de propriété ne peut pas invalider le paiement d'autrui.
    if _checkout_owner_ok(request, enrollment):
        mark_payment_failed(payment)
        messages.warning(request, "Paiement non confirmé. Réessayez ou choisissez un autre moyen.")
    return redirect("payments:checkout", enrollment_id=enrollment.pk)


@csrf_exempt
@require_POST
def flutterwave_webhook(request):
    if not providers.flutterwave_webhook_is_authentic(request):
        return _json_response({"detail": "invalid signature"}, 403)

    try:
        payload = json.loads(request.body.decode())
    except (ValueError, UnicodeDecodeError):
        return _json_response({"detail": "bad payload"}, 400)

    data = payload.get("data") or {}
    event = payload.get("event") or data.get("event")
    if event != "charge.completed":
        return _json_response({"detail": "ignored"})

    status = data.get("status") or payload.get("status")
    if status != "successful":
        return _json_response({"detail": "ignored"})

    tx_ref = data.get("tx_ref", "")
    transaction_id = data.get("id") or data.get("transaction_id") or ""

    verified = providers.flutterwave_verify(tx_ref, transaction_id=transaction_id)
    if verified is not None:
        mark_payment_success(
            verified,
            provider_ref=str(transaction_id or tx_ref),
            raw=payload,
        )
        return _json_response({"detail": "ok"})

    return _json_response({"detail": "unverified"}, 202)


# ---------------------------------------------------------------------------
# KkiaPay: data_return callback + webhook
# ---------------------------------------------------------------------------

@csrf_exempt
def kkiapay_callback(request, payment_id):
    """
    Retour navigateur de KkiaPay après paiement.

    KkiaPay peut renvoyer :
    - state=SUCCESS  -> on vérifie côté serveur
    - state=FAILED   -> on marque failed
    - ?data=<token>  -> on identifie notre paiement par le token
    """
    payment = (
        Payment.objects
        .filter(pk=payment_id)
        .select_related("enrollment__student")
        .first()
    )
    if payment is None:
        return redirect("home")

    enrollment = payment.enrollment

    if payment.status == "success":
        _finalize_owner_session(request, enrollment)
        return redirect("payments:success", enrollment_id=enrollment.pk)

    source = request.POST if request.method == "POST" else request.GET

    returned_data = source.get("data", "")
    state = str(source.get("state", "")).upper()

    # Si KkiaPay renvoie explicitement un échec, on arrête là
    if state in ("FAILED", "CANCELLED", "CANCELED"):
        if _checkout_owner_ok(request, enrollment):
            mark_payment_failed(payment)
            messages.warning(request, "Paiement annulé ou refusé. Réessayez.")
        return redirect("payments:checkout", enrollment_id=enrollment.pk)

    # Vérification du token secret
    if (
        returned_data
        and _HEX32.match(returned_data)
        and returned_data != payment.verification_token
    ):
        if _checkout_owner_ok(request, enrollment):
            mark_payment_failed(payment)
            messages.warning(request, "Réponse de paiement invalide. Réessayez.")
        return redirect("payments:checkout", enrollment_id=enrollment.pk)

    confirmation_id = (
        source.get("transaction_id")
        or source.get("transactionId")
        or source.get("id")
        or payment.provider_ref
        or ""
    )

    # Tentative de vérification serveur
    if providers.verify_kkiapay_payment(payment, confirmation_id):
        enrollment = mark_payment_success(
            payment,
            provider_ref=confirmation_id,
            raw=dict(source.items()),
        )
        _finalize_owner_session(request, enrollment)
        return redirect("payments:success", enrollment_id=enrollment.pk)

    # Non confirmé immédiatement — on laisse le webhook faire le travail
    request.session["pending_payment_id"] = payment.pk
    messages.info(request, "Paiement en cours de confirmation…")
    return redirect("payments:success", enrollment_id=enrollment.pk)


@csrf_exempt
@require_POST
def kkiapay_webhook(request, payment_id=None):
    """
    Webhook KkiaPay (une seule URL configurée dans le dashboard).

    Payload officiel : {"transactionId", "isPaymentSucces", "amount", "partnerId",
    "stateData", "event": "transaction.success" | "transaction.failed", ...}
    On ne croit JAMAIS le payload : le statut est re-vérifié via l'API serveur.
    """
    if not providers.kkiapay_webhook_is_authentic(request):
        return _json_response({"detail": "invalid signature"}, 403)

    payload = None
    if request.POST:
        payload = dict(request.POST.items())
    else:
        try:
            payload = json.loads(request.body.decode())
        except (ValueError, UnicodeDecodeError):
            pass
    if not isinstance(payload, dict) or not payload:
        return _json_response({"detail": "bad payload"}, 400)

    transaction_id = str(payload.get("transactionId") or payload.get("transaction_id") or "")
    if not transaction_id:
        return _json_response({"detail": "ignored"})

    # Rattachement au paiement : id dans l'URL (ancien format), sinon partnerId /
    # stateData (notre token), sinon transaction déjà connue.
    payment = None
    if payment_id is not None:
        payment = Payment.objects.filter(pk=payment_id, provider="kkiapay") \
            .select_related("enrollment__student").first()
    if payment is None:
        candidates = [
            str(payload.get("partnerId") or ""),
            str(payload.get("data") or ""),
        ]
        state_data = payload.get("stateData")
        if isinstance(state_data, dict):
            candidates.extend(str(v) for v in state_data.values())
        elif state_data:
            candidates.append(str(state_data))
        for token in filter(None, candidates):
            payment = Payment.objects.filter(provider="kkiapay", verification_token=token) \
                .select_related("enrollment__student").first()
            if payment:
                break
    if payment is None:
        payment = Payment.objects.filter(provider="kkiapay", provider_ref=transaction_id) \
            .select_related("enrollment__student").first()
    if payment is None:
        # Transaction étrangère à la plateforme : 200 pour éviter les relances.
        return _json_response({"detail": "unknown transaction"})

    data = providers.kkiapay_fetch_transaction(transaction_id)
    if data is None:
        return _json_response({"detail": "unverified"}, 202)

    state = providers.kkiapay_transaction_status(data)
    if state == "failed":
        mark_payment_failed(payment, provider_ref=transaction_id, raw=payload)
        return _json_response({"detail": "failed recorded"})

    if state == "success" and providers.verify_kkiapay_payment(payment, transaction_id):
        mark_payment_success(payment, provider_ref=transaction_id, raw=payload)
        return _json_response({"detail": "ok"})

    return _json_response({"detail": "unverified"}, 202)


def success(request, enrollment_id):
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("plan", "student"),
        pk=enrollment_id,
    )
    if not _checkout_owner_ok(request, enrollment):
        return redirect("accounts:login")

    _finalize_owner_session(request, enrollment)

    latest_payment = (
        enrollment.payments
        .exclude(status="failed")
        .order_by("-created_at")
        .first()
    )

    return render(request, "payments/success.html", {
        "enrollment": enrollment,
        "latest_payment": latest_payment,
    })
