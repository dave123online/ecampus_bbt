"""
Provider adapters for self-service payments.
"""
import hmac
import logging
import re
from decimal import Decimal, InvalidOperation
import requests
from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Money helpers
# ---------------------------------------------------------------------------

def compute_tranche_amounts(enrollment):
    price = enrollment.plan.price
    n = max(1, enrollment.plan.max_installments)
    if n == 1:
        return [price]
    first = (price * Decimal(str(settings.FIRST_TRANCHE_RATIO))).quantize(Decimal("0.01"))
    rest = price - first
    per_rest = (rest / (n - 1)).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
    tranches = [first] + [per_rest] * (n - 2)
    tranches.append(price - sum(tranches))
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
# Flutterwave helpers
# ---------------------------------------------------------------------------

def _flutterwave_headers():
    return {
        "Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}",
    }


def _payment_from_flw_tx_ref(tx_ref):
    if not tx_ref:
        return None
    parts = tx_ref.split("-", 2)
    if len(parts) != 3 or parts[0] != "flw":
        return None
    try:
        payment_id = int(parts[1])
    except (TypeError, ValueError):
        return None
    token = parts[2]
    from .models import Payment
    return (
        Payment.objects
        .filter(id=payment_id, verification_token=token)
        .select_related("enrollment__plan")
        .first()
    )


def _flw_get(path, params=None):
    try:
        resp = requests.get(
            settings.FLUTTERWAVE_BASE_URL + path,
            headers=_flutterwave_headers(),
            params=params,
            timeout=15,
        )
        return resp.status_code, resp.json()
    except (requests.RequestException, ValueError):
        logger.exception("Flutterwave API call failed: %s", path)
        return None, {}


def _flw_data_matches(data, payment, tx_ref):
    if not isinstance(data, dict):
        return False
    if data.get("tx_ref") != tx_ref:
        return False
    if data.get("status") != "successful":
        return False
    amount_raw = data.get("charged_amount", data.get("amount"))
    if amount_raw is None:
        return False
    try:
        amount = Decimal(str(amount_raw))
    except (InvalidOperation, ValueError):
        return False
    if amount != payment.amount:
        return False
    currency = data.get("currency")
    if currency and currency != payment.currency:
        return False
    return True


def flutterwave_initiate_payment(payment, email, name, phone):
    base = settings.PLATFORM_BASE_URL.rstrip("/")
    payload = {
        "tx_ref": f"flw-{payment.id}-{payment.verification_token}",
        "amount": str(payment.amount),
        "currency": payment.currency,
        "redirect_url": base + reverse("payments:flutterwave_callback", args=[payment.id]),
        "customer": {
            "email": email,
            "name": name,
            "phonenumber": phone,
        },
        "customizations": {
            "title": "eCampus BBT",
            "description": "Accès plateforme",
        },
        "meta": {
            "payment_id": payment.id,
        },
    }
    try:
        resp = requests.post(
            settings.FLUTTERWAVE_BASE_URL + "/payments",
            json=payload,
            headers=_flutterwave_headers(),
            timeout=15,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.exception("Flutterwave initiate failed")
        raise RuntimeError("Le prestataire de paiement est momentanément indisponible.") from exc

    if resp.status_code not in (200, 201) or data.get("status") != "success":
        logger.error("Flutterwave initiate failed: %s %s", resp.status_code, data)
        raise RuntimeError("Le prestataire de paiement est momentanément indisponible.")

    link = (data.get("data") or {}).get("link")
    if not link:
        logger.error("Flutterwave initiate response has no payment link: %s", data)
        raise RuntimeError("Le prestataire de paiement est momentanément indisponible.")
    return link


def flutterwave_verify(tx_ref, transaction_id=None):
    payment = _payment_from_flw_tx_ref(tx_ref)
    if payment is None:
        return None

    if transaction_id:
        status_code, body = _flw_get(f"/transactions/{transaction_id}/verify")
        data = (body or {}).get("data") or {}
        if status_code == 200 and _flw_data_matches(data, payment, tx_ref):
            return payment

    status_code, body = _flw_get("/transactions", params={"tx_ref": tx_ref})
    rows = (body or {}).get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("transactions") or rows.get("results") or []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("tx_ref") != tx_ref:
                continue
            flw_id = row.get("id")
            if flw_id:
                verify_status, verify_body = _flw_get(f"/transactions/{flw_id}/verify")
                verify_data = (verify_body or {}).get("data") or {}
                if verify_status == 200 and _flw_data_matches(verify_data, payment, tx_ref):
                    return payment
            if _flw_data_matches(row, payment, tx_ref):
                return payment
    return None


def flutterwave_webhook_is_authentic(request) -> bool:
    given = request.headers.get("verif-hash", "")
    expected = getattr(settings, "FLUTTERWAVE_SECRET_HASH", "")
    if not expected or not given:
        return False
    return hmac.compare_digest(given, expected)


# ---------------------------------------------------------------------------
# KkiaPay
# ---------------------------------------------------------------------------
# Flux officiel (docs.kkiapay.me) :
#   1. le navigateur ouvre le widget JS (clé publique) -> le client paie ;
#   2. KkiaPay renvoie un transactionId ;
#   3. le SERVEUR vérifie la transaction : POST {base}/api/v1/transactions/status
#      (en-têtes X-API-KEY / X-PRIVATE-KEY / X-SECRET-KEY, champ transactionId)
#      -> {"status": "SUCCESS", "amount": 100, "transactionId": "...", ...}
# Le webhook (dashboard) ne sert que de notification : on re-vérifie toujours via l'API.

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def kkiapay_widget_config(payment, student) -> dict:
    """Paramètres du widget JS KkiaPay pour ce paiement (rendus côté navigateur)."""
    base = settings.PLATFORM_BASE_URL.rstrip("/")
    cfg = {
        "key": settings.KKIAPAY_PUBLIC_KEY,
        "amount": int(payment.amount),          # XOF, sans décimales
        "sandbox": bool(settings.KKIAPAY_SANDBOX),
        "callback": base + reverse("payments:kkiapay_callback", args=[payment.id]),
        # partnerId / data : identifiant interne, renvoyé par KkiaPay -> sert à rattacher
        # la transaction à CE paiement (anti-rejeu).
        "partnerId": payment.verification_token,
        "data": payment.verification_token,
        "name": student.first_name or student.username,
        "email": student.email or "",
        "phone": (student.phone_number or "").replace(" ", "").replace("-", "").lstrip("+"),
        "paymentmethod": "momo",
        "theme": "#0f766e",
    }
    return cfg


def kkiapay_fetch_transaction(transaction_id):
    """Interroge l'API KkiaPay. Retourne le dict de la transaction ou None."""
    if not transaction_id:
        return None
    try:
        resp = requests.post(
            f"{settings.KKIAPAY_BASE_URL}/api/v1/transactions/status",
            data={"transactionId": str(transaction_id)},
            headers={
                "Accept": "application/json",
                "X-API-KEY": settings.KKIAPAY_PUBLIC_KEY,
                "X-PRIVATE-KEY": settings.KKIAPAY_PRIVATE_KEY,
                "X-SECRET-KEY": settings.KKIAPAY_SECRET_KEY,
            },
            timeout=15,
        )
        data = resp.json()
    except (requests.RequestException, ValueError):
        logger.exception("KkiaPay status failed for transaction=%s", transaction_id)
        return None
    if resp.status_code != 200 or not isinstance(data, dict):
        logger.warning("KkiaPay status HTTP %s for transaction=%s: %s",
                       resp.status_code, transaction_id, str(data)[:300])
        return None
    return data


def _kkiapay_returned_refs(data):
    """Valeurs d'identifiant que KkiaPay a pu renvoyer (partnerId / data / stateData)."""
    out = []
    for key in ("partnerId", "data", "stateData"):
        val = data.get(key)
        if isinstance(val, dict):
            out.extend(str(v) for v in val.values())
        elif val not in (None, ""):
            out.append(str(val))
    return out


def kkiapay_transaction_status(data) -> str:
    """'success' | 'failed' | 'pending' à partir de la réponse de l'API."""
    status = str(data.get("status") or data.get("state") or "").upper()
    if status in ("SUCCESS", "SUCCESSFUL", "COMPLETED") or data.get("state") is True:
        return "success"
    if status in ("FAILED", "FAILURE", "CANCELLED", "CANCELED", "EXPIRED"):
        return "failed"
    return "pending"


def verify_kkiapay_payment(payment, confirmation_id) -> bool:
    """Vérification serveur d'une transaction KkiaPay pour CE paiement.

    Garde-fous :
    - transaction jamais rattachée à un AUTRE paiement (anti-rejeu) ;
    - statut SUCCESS et montant exact ;
    - si KkiaPay renvoie un de NOS identifiants (32 hex) il doit être celui de ce paiement.
    """
    if not confirmation_id:
        return False
    confirmation_id = str(confirmation_id)

    from .models import Payment

    if (
        Payment.objects
        .filter(provider="kkiapay", provider_ref=confirmation_id)
        .exclude(pk=payment.pk)
        .exists()
    ):
        logger.warning(
            "KkiaPay replay refused: tx=%s already bound to another payment (payment=%s)",
            confirmation_id, payment.pk,
        )
        return False

    data = kkiapay_fetch_transaction(confirmation_id)
    if data is None:
        return False

    if kkiapay_transaction_status(data) != "success":
        return False

    try:
        amount_ok = Decimal(str(data.get("amount"))) == Decimal(int(payment.amount))
    except (InvalidOperation, ValueError, TypeError):
        amount_ok = False
    if not amount_ok:
        logger.warning("KkiaPay amount mismatch: tx=%s got=%s expected=%s",
                       confirmation_id, data.get("amount"), payment.amount)
        return False

    for ref in _kkiapay_returned_refs(data):
        if _HEX32.match(ref) and ref != payment.verification_token:
            logger.warning("KkiaPay token mismatch: tx=%s payment=%s", confirmation_id, payment.pk)
            return False

    return True


def kkiapay_webhook_is_authentic(request) -> bool:
    """En-tête x-kkiapay-secret == hash secret du dashboard (si configuré).

    Défense en profondeur : le webhook n'est jamais cru sur parole,
    la transaction est toujours re-vérifiée via l'API.
    """
    expected = getattr(settings, "KKIAPAY_WEBHOOK_SECRET", "")
    if not expected:
        return True
    given = request.headers.get("x-kkiapay-secret", "")
    return bool(given) and hmac.compare_digest(given, expected)
