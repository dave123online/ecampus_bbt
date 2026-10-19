"""
Provider adapters for self-service payments.

Security model — defense in depth:
1. The client NEVER chooses the amount. Amounts are computed server-side
   from the Enrollment (tranche 1 = ceil(ratio × price), final tranche =
   exact remainder).
2. A payment is only marked `success` after a SERVER-SIDE verification
   against the provider's API (verify endpoint) — and, for Flutterwave,
   additionally via an HMAC-signed webhook. A redirect to our callback URL
   alone never grants access.
3. Webhooks are deduplicated by provider_ref and validated with a constant
   -time HMAC comparison.

NOTE ON KKIAPAY: KkiaPay's public integration posts an inline payment form
(fields: apikey, amount, customer_phone, data_return=url de confirmation, …)
to https://app.kkiapay.me/api/tpayment. Exact field names / signature scheme
may vary with your merchant account version — check their official doc and
adjust `_kkiapay_form_fields()` and `verify_kkiapay_payment()` below. All
other logic (amounts, tranches, unlocking) is provider-independent.
"""

import hmac
import logging
from decimal import Decimal

import requests
from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Money helpers
# ---------------------------------------------------------------------------

def compute_tranche_amounts(enrollment):
    """Return a list of Decimal amounts, one per tranche allowed by the plan.

    Tranche 1 pays FIRST_TRANCHE_RATIO (~1/3) so the student gets ~1/3 of
    the notions; remaining tranches split the rest evenly; the LAST tranche
    always equals the exact remainder so the sum == plan.price.
    """
    price = enrollment.plan.price
    n = max(1, enrollment.plan.max_installments)
    if n == 1:
        return [price]
    first = (price * Decimal(str(settings.FIRST_TRANCHE_RATIO))).quantize(Decimal("0.01"))
    rest = price - first
    per_rest = (rest / (n - 1)).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
    tranches = [first] + [per_rest] * (n - 2)
    tranches.append(price - sum(tranches))  # exact remainder
    return tranches


def next_tranche_state(enrollment):
    """Return (tranche_index, amount) for the next payable tranche,
    or (None, None) if fully paid / no slot left."""
    used = enrollment.installments_used
    total = enrollment.plan.max_installments
    if enrollment.total_paid >= enrollment.plan.price or used >= total:
        return None, None
    tranches = compute_tranche_amounts(enrollment)
    return used + 1, tranches[used]


# ---------------------------------------------------------------------------
# Flutterwave (international cards — incl. prepaid — + mobile money Africa)
# ---------------------------------------------------------------------------

def flutterwave_initiate_payment(payment, email, name, phone):
    """Create a transaction via the standard FLW API v3. Returns redirect URL.

    txRef embeds the secret verification_token so the callback can be
    matched AND verified server-side afterwards.
    """
    base = settings.PLATFORM_BASE_URL.rstrip("/")
    payload = {
        "tx_ref": f"flw-{payment.id}-{payment.verification_token}",
        "amount": str(payment.amount),
        "currency": payment.currency,
        "redirect_url": base + reverse("payments:flutterwave_callback", args=[payment.id]),
        "customer": {"email": email, "name": name, "phonenumber": phone},
        "customizations": {"title": "eCampus BBT", "description": "Accès plateforme"},
        "meta": {"payment_id": payment.id},
    }
    resp = requests.post(
        settings.FLUTTERWAVE_BASE_URL + "/transactions/initiate",
        json=payload,
        headers={"Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}"},
        timeout=15,
    )
    data = resp.json()
    if resp.status_code != 200 or data.get("status") != "success":
        logger.error("Flutterwave initiate failed: %s %s", resp.status_code, data)
        raise RuntimeError("Le prestataire de paiement est momentanément indisponible.")
    return data["data"]["link"]


def flutterwave_verify(tx_ref):
    """Server-side verification of a completed transaction.

    Fetches the transaction by ID and checks: status successful,
    amount matches, tx_ref carries OUR secret token. This is what makes
    a forged redirect useless.
    """
    try:
        flw_id = tx_ref.split("-")[1]
        token = tx_ref.split("-", 2)[2]
    except IndexError:
        return None
    from .models import Payment

    payment = Payment.objects.filter(id=flw_id, verification_token=token).first()
    if payment is None:
        return None
    resp = requests.get(
        f"{settings.FLUTTERWAVE_BASE_URL}/transactions/{flw_id}/verify",
        headers={"Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}"},
        timeout=15,
    )
    data = resp.json().get("data") or {}
    if (
        data.get("status") == "successful"
        and Decimal(str(data.get("charged_amount", 0))) == payment.amount
        and data.get("tx_ref") == tx_ref
    ):
        return payment
    return None


def flutterwave_webhook_is_authentic(request) -> bool:
    """Flutterwave sends a static `verif-hash` header configured in the
    dashboard. Constant-time comparison; empty secret => reject everything."""
    given = request.headers.get("verif-hash", "")
    expected = getattr(settings, "FLUTTERWAVE_SECRET_HASH", "")
    if not expected or not given:
        return False
    return hmac.compare_digest(given, expected)


# ---------------------------------------------------------------------------
# KkiaPay (Mobile Money — Bénin / UEMOA)
# ---------------------------------------------------------------------------

def kkiapay_initiate_payment(payment, phone):
    """Return (post_url, fields) for the auto-submitted inline payment form.

    The customer is redirected to KkiaPay's hosted page; after payment it
    POSTs back to `data_return` with a confirmation id (`data`).
    """
    base = settings.PLATFORM_BASE_URL.rstrip("/")
    fields = _kkiapay_form_fields(payment, phone, base)
    return "https://app.kkiapay.me/api/tpayment", fields


def _kkiapay_form_fields(payment, phone, base):
    """ADAPT to your KkiaPay merchant doc — canonical inline-form fields."""
    return {
        "apikey": settings.KKIAPAY_PUBLIC_KEY,
        "amount": str(int(payment.amount)),          # XOF, no decimals
        "phone_number": phone,
        "callback": base + reverse("payments:kkiapay_webhook", args=[payment.id]),
        "data_return": base + reverse("payments:kkiapay_callback", args=[payment.id]),
        "data": payment.verification_token,           # echoed back on return
        "object": f"eCampus BBT — tranche {payment.tranche_index}",
    }


def verify_kkiapay_payment(payment, confirmation_id) -> bool:
    """Server-side check of a KkiaPay transaction by its confirmation id.

    Endpoint/params follow KkiaPay's transactions API (GET with
    `transaction_id` + private key header). ADAPT to current doc.
    """
    if not confirmation_id:
        return False
    resp = requests.get(
        f"{settings.KKIAPAY_BASE_URL}/transactions/{confirmation_id}",
        headers={"privatekey": settings.KKIAPAY_SECRET_KEY},
        timeout=15,
    )
    try:
        data = resp.json()
    except ValueError:
        return False
    # Expected shape (to confirm with doc): {"state": true, "amount": ..., ...}
    confirmed = bool(data.get("state")) and str(data.get("amount")) == str(int(payment.amount))
    return confirmed


def kkiapay_lookup_by_token(payment):
    """Best-effort recovery of a KkiaPay confirmation id when the browser
    callback/webhook never delivered one (lost POST).

    Some KkiaPay account versions expose a transaction list filtered by the
    phone number; we look for a matching amount after initiation. If your
    account has no such endpoint, this returns "" and the async safety-net
    command (payments_recheck_pending) handles expiry instead.
    """
    if payment.provider_ref:  # already known from a partial webhook
        return payment.provider_ref
    try:
        resp = requests.get(
            f"{settings.KKIAPAY_BASE_URL}/transactions",
            headers={"privatekey": settings.KKIAPAY_SECRET_KEY},
            params={"phone_number": payment.enrollment.student.phone_number},
            timeout=15,
        )
        data = resp.json()
    except (ValueError, requests.RequestException):
        return ""
    rows = data.get("data") or data if isinstance(data, (list, dict)) else []
    if isinstance(rows, dict):
        rows = rows.get("transactions", []) or rows.get("results", []) or []
    target = str(int(payment.amount))
    for row in rows:
        if str(row.get("amount")) == target and row.get("id"):
            return str(row["id"])
    return ""
