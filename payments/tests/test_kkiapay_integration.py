from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import CustomUser
from payments.models import Enrollment, Payment, PaymentPlan


@override_settings(
    KKIAPAY_PUBLIC_KEY="pk_test", KKIAPAY_PRIVATE_KEY="prv", KKIAPAY_SECRET_KEY="sec",
    KKIAPAY_SANDBOX=True, KKIAPAY_WEBHOOK_SECRET="",
    KKIAPAY_BASE_URL="https://api-sandbox.kkiapay.me",
)
class KkiaPayWidgetFlowTest(TestCase):
    def setUp(self):
        self.plan = PaymentPlan.objects.create(
            name="Pack A1", price=Decimal("30000"), notion_count=10, max_installments=3)
        self.student = CustomUser.objects.create_user(
            username="test@example.com", email="test@example.com", password="test123",
            role="student", level="A1", is_active=True, phone_number="+22997000000")
        self.enrollment = Enrollment.objects.create(
            student=self.student, plan=self.plan, status="pending")
        self.client = Client()
        self.client.force_login(self.student)

    def _checkout(self):
        return self.client.post(
            reverse("payments:checkout", args=[self.enrollment.pk]), {"provider": "kkiapay"})

    def test_checkout_redirects_to_widget_page(self):
        r = self._checkout()
        payment = Payment.objects.get()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("payments:kkiapay_pay", args=[payment.pk]))

    def test_widget_page_exposes_public_config_only(self):
        self._checkout()
        payment = Payment.objects.get()
        r = self.client.get(reverse("payments:kkiapay_pay", args=[payment.pk]))
        self.assertEqual(r.status_code, 200)
        cfg = r.context["widget"]
        self.assertEqual(cfg["key"], "pk_test")
        self.assertEqual(cfg["amount"], int(payment.amount))
        self.assertTrue(cfg["sandbox"])
        self.assertEqual(cfg["partnerId"], payment.verification_token)
        self.assertIn(reverse("payments:kkiapay_callback", args=[payment.pk]), cfg["callback"])
        body = r.content.decode()
        self.assertNotIn("prv", body.replace("provider", ""))   # clé privée jamais exposée
        self.assertIn("cdn.kkiapay.me/k.js", body)

    def test_widget_page_requires_owner(self):
        self._checkout()
        payment = Payment.objects.get()
        r = Client().get(reverse("payments:kkiapay_pay", args=[payment.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("kkiapay", r.url.split("/pay/")[0] if "/pay/" in r.url else r.url)

    @override_settings(KKIAPAY_PUBLIC_KEY="")
    def test_missing_public_key_fails_gracefully(self):
        self._checkout()
        payment = Payment.objects.get()
        r = self.client.get(reverse("payments:kkiapay_pay", args=[payment.pk]))
        self.assertEqual(r.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "failed")

    @patch("payments.providers.requests.post")
    def test_callback_with_transaction_id_confirms_payment(self, mpost):
        self._checkout()
        payment = Payment.objects.get()
        mpost.return_value = Mock(status_code=200, json=Mock(return_value={
            "status": "SUCCESS", "amount": int(payment.amount), "transactionId": "TXOK"}))
        r = self.client.get(
            reverse("payments:kkiapay_callback", args=[payment.pk]), {"transaction_id": "TXOK"})
        self.assertEqual(r.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "success")
        self.assertEqual(payment.provider_ref, "TXOK")
        # appel API conforme à la doc officielle
        args, kwargs = mpost.call_args
        self.assertEqual(args[0], "https://api-sandbox.kkiapay.me/api/v1/transactions/status")
        self.assertEqual(kwargs["data"], {"transactionId": "TXOK"})
        self.assertEqual(kwargs["headers"]["X-PRIVATE-KEY"], "prv")
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "pk_test")
        self.assertEqual(kwargs["headers"]["X-SECRET-KEY"], "sec")


@override_settings(
    KKIAPAY_PUBLIC_KEY="pk_test", KKIAPAY_PRIVATE_KEY="prv", KKIAPAY_SECRET_KEY="sec",
    KKIAPAY_BASE_URL="https://api.kkiapay.me",
)
class KkiaPayWebhookTest(TestCase):
    def setUp(self):
        plan = PaymentPlan.objects.create(
            name="P", price=Decimal("30000"), notion_count=9, max_installments=3)
        student = CustomUser.objects.create_user(
            username="w@x.com", email="w@x.com", password="x", role="student",
            level="A1", is_active=False)
        enrollment = Enrollment.objects.create(student=student, plan=plan)
        self.payment = Payment.objects.create(
            enrollment=enrollment, provider="kkiapay", amount=Decimal("10200"))
        self.url = reverse("payments:kkiapay_webhook_generic")

    def _payload(self, event="transaction.success", tx="TXW"):
        return {"transactionId": tx, "isPaymentSucces": event.endswith("success"),
                "amount": 10200, "partnerId": self.payment.verification_token,
                "stateData": {}, "event": event}

    @patch("payments.providers.requests.post")
    def test_official_payload_confirms_payment(self, mpost):
        mpost.return_value = Mock(status_code=200, json=Mock(return_value={
            "status": "SUCCESS", "amount": 10200, "transactionId": "TXW"}))
        r = self.client.post(self.url, self._payload(), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "success")
        self.assertTrue(self.payment.enrollment.student.__class__.objects
                        .get(pk=self.payment.enrollment.student_id).is_active)

    @patch("payments.providers.requests.post")
    def test_forged_success_not_confirmed_by_api(self, mpost):
        mpost.return_value = Mock(status_code=200, json=Mock(return_value={
            "status": "FAILED", "amount": 10200, "transactionId": "TXW"}))
        self.client.post(self.url, self._payload(), content_type="application/json")
        self.payment.refresh_from_db()
        self.assertNotEqual(self.payment.status, "success")

    @patch("payments.providers.requests.post")
    def test_failed_event_recorded_only_if_api_agrees(self, mpost):
        mpost.return_value = Mock(status_code=200, json=Mock(return_value={
            "status": "FAILED", "amount": 10200, "transactionId": "TXW"}))
        self.client.post(
            self.url, self._payload("transaction.failed"), content_type="application/json")
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "failed")

    @override_settings(KKIAPAY_WEBHOOK_SECRET="hash-secret")
    def test_signature_required_when_configured(self):
        r = self.client.post(self.url, self._payload(), content_type="application/json")
        self.assertEqual(r.status_code, 403)
        with patch("payments.providers.requests.post") as mpost:
            mpost.return_value = Mock(status_code=200, json=Mock(return_value={
                "status": "SUCCESS", "amount": 10200}))
            r = self.client.post(self.url, self._payload(), content_type="application/json",
                                 HTTP_X_KKIAPAY_SECRET="hash-secret")
        self.assertEqual(r.status_code, 200)

    def test_unknown_transaction_is_acknowledged(self):
        r = self.client.post(
            self.url, {"transactionId": "ZZZ", "partnerId": "inconnu"},
            content_type="application/json")
        self.assertEqual(r.status_code, 200)
