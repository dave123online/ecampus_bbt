import secrets

from decimal import Decimal

from django.conf import settings
from django.db import models


def _verification_token():
    """Cryptographically random secret used server-side to verify a
    transaction with the provider. Never exposed to templates/clients."""
    return secrets.token_hex(16)


class PaymentPlan(models.Model):
    """A purchasable access plan defined by the manager.

    `notion_count` = how many notions of the student's level are unlocked
    when this plan is fully paid. Plans are cumulative: a student who buys
    two plans gets the sum of their notion counts.
    """
    name = models.CharField(max_length=120, help_text="Ex. « Pack A1 — 10 notions »")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="XOF")
    notion_count = models.PositiveIntegerField(
        help_text="Nombre de notions débloquées quand ce plan est intégralement payé."
    )
    max_installments = models.PositiveSmallIntegerField(
        default=1, choices=[(1, "Paiement unique"), (2, "2 tranches"), (3, "3 tranches")],
        help_text="Nombre maximum de tranches pour ce plan.",
    )
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} — {self.price} {self.currency}"


class Enrollment(models.Model):
    """One self-registered student buying one plan.

    The student account is created immediately (pending payment) so the
    checkout can be resumed; it is flagged `is_active=False` until the
    first verified payment lands. Access to content is governed by
    `paid_notion_count`, never by manual admin validation.
    """
    STATUS_CHOICES = [
        ("pending", "En attente de paiement"),
        ("partially_paid", "Partiellement payé"),
        ("paid", "Intégralement payé"),
        ("cancelled", "Annulé"),
    ]

    student = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollment"
    )
    plan = models.ForeignKey(PaymentPlan, on_delete=models.PROTECT, related_name="enrollments")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(null=True, blank=True,
                                            help_text="Dernière transaction initiée/confirmée "
                                                      "(base du nettoyage automatique).")

    def __str__(self):
        return f"Inscription {self.student.username} — {self.plan.name} ({self.status})"

    # ---- money helpers (single source of truth: Payment rows) ----
    @property
    def total_paid(self) -> Decimal:
        agg = self.payments.filter(status="success").aggregate(
            s=models.Sum("amount", default=Decimal("0"))
        )
        return agg["s"]

    @property
    def installments_used(self) -> int:
        return self.payments.filter(status__in=["initiated", "success"]).count()

    @property
    def remaining(self) -> Decimal:
        return max(Decimal("0"), self.plan.price - self.total_paid)

    def paid_notion_count(self) -> int:
        """Number of notions the student has validly paid for (floor of
        the paid ratio × plan size)."""
        if self.plan.price <= 0:
            return self.plan.notion_count
        ratio = min(Decimal("1"), self.total_paid / self.plan.price)
        units = (ratio * self.plan.notion_count).quantize(Decimal("1"), rounding="ROUND_DOWN")
        return int(units)

    def refresh_status(self):
        paid = self.total_paid
        if paid >= self.plan.price:
            self.status = "paid"
        elif paid > 0:
            self.status = "partially_paid"
        else:
            self.status = "pending"
        self.save(update_fields=["status"])


class Payment(models.Model):
    """A single transaction (one tranche) against an Enrollment.

    Status transitions: initiated -> success | failed.
    Only a webhook verified against the provider's signature (see
    payments/providers.py) may set `success` — never client-side code.
    """
    STATUS_CHOICES = [
        ("initiated", "Initiée"),
        ("success", "Confirmée"),
        ("failed", "Échouée"),
    ]
    PROVIDER_CHOICES = [
        ("kkiapay", "KkiaPay (Mobile Money Bénin)"),
        ("flutterwave", "Flutterwave (carte internationale)"),
    ]

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=15, choices=PROVIDER_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="XOF")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="initiated")
    tranche_index = models.PositiveSmallIntegerField(default=1, help_text="1, 2 ou 3")

    # Provider references — used to verify webhooks and deduplicate.
    provider_ref = models.CharField(max_length=190, blank=True,
                                    help_text="ID/txRef renvoyé par le prestataire.")
    verification_token = models.CharField(max_length=64, unique=True, default=_verification_token,
                                          editable=False,
                                          help_text="Token secret utilisé pour vérifier "
                                                    "le paiement côté serveur (API verify).")
    raw_webhook = models.JSONField(null=True, blank=True,
                                   help_text="Payload brut reçu, pour audit.")
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            # One live tranche per (enrollment, tranche number) — prevents
            # double-spending the installment slots.
            models.UniqueConstraint(
                fields=["enrollment", "tranche_index"],
                condition=models.Q(status__in=["initiated", "success"]),
                name="unique_live_tranche_per_enrollment",
            ),
        ]

    def __str__(self):
        return f"{self.provider} {self.amount} {self.currency} — {self.get_status_display()}"
