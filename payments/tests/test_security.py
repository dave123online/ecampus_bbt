"""Non-régression sécurité : callbacks de paiement, rejeu KkiaPay, rate limiting."""
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import CustomUser
from payments.models import Enrollment, Payment, PaymentPlan
from payments.providers import verify_kkiapay_payment
from payments.services import mark_payment_success


def _setup(case, active=True):
    case.plan = PaymentPlan.objects.create(
        name="P", price=Decimal("30000"), notion_count=9, max_installments=3)
    case.student = CustomUser.objects.create_user(
        username="v@x.com", email="v@x.com", password="pass12345",
        role="student", level="A1", is_active=active, phone_number="+22997000000")
    case.enrollment = Enrollment.objects.create(student=case.student, plan=case.plan)


class CallbackTakeoverTests(TestCase):
    """Un visiteur anonyme ne doit jamais être connecté via un callback."""

    def setUp(self):
        _setup(self)
        self.paid = Payment.objects.create(
            enrollment=self.enrollment, provider="kkiapay",
            amount=Decimal("10200"), status="success", tranche_index=1)

    def test_anonymous_kkiapay_callback_does_not_log_in(self):
        c = Client()
        resp = c.get(reverse("payments:kkiapay_callback", args=[self.paid.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("_auth_user_id", c.session)

    def test_anonymous_flutterwave_callback_does_not_log_in(self):
        self.paid.provider = "flutterwave"
        self.paid.save()
        c = Client()
        c.get(reverse("payments:flutterwave_callback", args=[self.paid.pk]))
        self.assertNotIn("_auth_user_id", c.session)

    def test_owner_session_still_logs_in_after_success(self):
        c = Client()
        s = c.session
        s["pending_enrollment_id"] = self.enrollment.pk
        s.save()
        c.get(reverse("payments:kkiapay_callback", args=[self.paid.pk]))
        self.assertEqual(int(c.session["_auth_user_id"]), self.student.pk)

    def test_anonymous_success_page_redirects_to_login(self):
        resp = Client().get(reverse("payments:success", args=[self.enrollment.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)


class CallbackForcedFailureTests(TestCase):
    """Un anonyme ne doit pas pouvoir faire échouer le paiement d'autrui."""

    def setUp(self):
        _setup(self, active=False)
        self.initiated = Payment.objects.create(
            enrollment=self.enrollment, provider="kkiapay", amount=Decimal("10200"))

    def test_anonymous_failed_state_ignored(self):
        Client().get(reverse("payments:kkiapay_callback", args=[self.initiated.pk]),
                     {"state": "FAILED"})
        self.initiated.refresh_from_db()
        self.assertEqual(self.initiated.status, "initiated")

    def test_anonymous_bad_token_ignored(self):
        Client().get(reverse("payments:kkiapay_callback", args=[self.initiated.pk]),
                     {"data": "nope"})
        self.initiated.refresh_from_db()
        self.assertEqual(self.initiated.status, "initiated")

    def test_anonymous_flutterwave_callback_without_ref_ignored(self):
        self.initiated.provider = "flutterwave"
        self.initiated.save()
        Client().get(reverse("payments:flutterwave_callback", args=[self.initiated.pk]))
        self.initiated.refresh_from_db()
        self.assertEqual(self.initiated.status, "initiated")

    def test_owner_failed_state_is_recorded(self):
        c = Client()
        s = c.session
        s["pending_enrollment_id"] = self.enrollment.pk
        s.save()
        c.get(reverse("payments:kkiapay_callback", args=[self.initiated.pk]),
              {"state": "FAILED"})
        self.initiated.refresh_from_db()
        self.assertEqual(self.initiated.status, "failed")

    def test_kkiapay_callback_accepts_cross_site_post(self):
        # KkiaPay peut renvoyer l'utilisateur en POST : pas de 403 CSRF.
        c = Client(enforce_csrf_checks=True)
        resp = c.post(reverse("payments:kkiapay_callback", args=[self.initiated.pk]), {})
        self.assertNotEqual(resp.status_code, 403)


def _kkiapay_ok(amount, data=None, status="SUCCESS"):
    body = {"status": status, "amount": amount, "transactionId": "TX"}
    if data is not None:
        body["partnerId"] = data
    return Mock(status_code=200, json=Mock(return_value=body))


class KkiaPayReplayTests(TestCase):
    def setUp(self):
        _setup(self)
        self.p1 = Payment.objects.create(
            enrollment=self.enrollment, provider="kkiapay", amount=Decimal("10200"),
            tranche_index=1)
        self.p2 = Payment.objects.create(
            enrollment=self.enrollment, provider="kkiapay", amount=Decimal("10200"),
            tranche_index=2)

    @patch("payments.providers.requests.post")
    def test_same_transaction_cannot_confirm_two_payments(self, mget):
        mget.return_value = _kkiapay_ok(10200)
        self.assertTrue(verify_kkiapay_payment(self.p1, "TX1"))
        mark_payment_success(self.p1, provider_ref="TX1")
        self.assertFalse(verify_kkiapay_payment(self.p2, "TX1"))

    @patch("payments.providers.requests.post")
    def test_db_constraint_blocks_race_on_same_transaction(self, mget):
        mark_payment_success(self.p1, provider_ref="TX1")
        mark_payment_success(self.p2, provider_ref="TX1")  # course : collision
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.status, "initiated")

    @patch("payments.providers.requests.post")
    def test_foreign_verification_token_rejected(self, mget):
        mget.return_value = _kkiapay_ok(10200, data="f" * 32)
        self.assertFalse(verify_kkiapay_payment(self.p1, "TX9"))

    @patch("payments.providers.requests.post")
    def test_matching_token_accepted(self, mget):
        mget.return_value = _kkiapay_ok(10200, data=self.p1.verification_token)
        self.assertTrue(verify_kkiapay_payment(self.p1, "TX9"))

    @patch("payments.providers.requests.post")
    def test_failed_status_rejected(self, mget):
        mget.return_value = _kkiapay_ok(10200, status="FAILED")
        self.assertFalse(verify_kkiapay_payment(self.p1, "TX9"))

    @patch("payments.providers.requests.post")
    def test_wrong_amount_rejected(self, mget):
        mget.return_value = _kkiapay_ok(5)
        self.assertFalse(verify_kkiapay_payment(self.p1, "TX9"))


@override_settings(LOGIN_RATE_LIMIT=(3, 60), REGISTER_RATE_LIMIT=(2, 60), RECOVERY_RATE_LIMIT=(2, 60))
class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_login_is_throttled(self):
        c = Client()
        codes = [c.post(reverse("accounts:login"), {"username": "a", "password": "b"}).status_code
                 for _ in range(5)]
        self.assertEqual(codes[-1], 429)
        self.assertNotIn(429, codes[:3])

    def test_recovery_is_throttled(self):
        c = Client()
        codes = [c.post(reverse("accounts:resend_recovery"), {"email": "a@b.com"}).status_code
                 for _ in range(4)]
        self.assertEqual(codes[-1], 429)

    def test_get_is_not_throttled(self):
        c = Client()
        for _ in range(6):
            self.assertEqual(c.get(reverse("accounts:login")).status_code, 200)
