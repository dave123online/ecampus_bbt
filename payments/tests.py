"""Tests for the self-service payment pipeline.

Run with: python manage.py test payments
No network access needed — provider HTTP calls are mocked.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import CustomUser
from courses.models import Notion
from progress.models import StudentProgress

from .models import Enrollment, Payment, PaymentPlan
from .providers import compute_tranche_amounts, next_tranche_state
from .services import (
    cancel_abandoned_enrollments, mark_payment_failed, mark_payment_success,
    paid_notions_available_for,
)


def make_plan(**kw):
    kw.setdefault("name", "Pack A1")
    kw.setdefault("price", Decimal("30000"))
    kw.setdefault("notion_count", 9)
    kw.setdefault("max_installments", 3)
    return PaymentPlan.objects.create(**kw)


def make_student(email="a@b.com", level="A1", active=True):
    return CustomUser.objects.create_user(
        username=email, email=email, password="pass12345",
        role="student", level=level, is_active=active, phone_number="+22997000000",
    )


class TrancheMathTests(TestCase):
    def test_three_tranches_sum_to_price(self):
        plan = make_plan(price=Decimal("100"), max_installments=3)
        student = make_student()
        enrollment = Enrollment.objects.create(student=student, plan=plan)
        tranches = compute_tranche_amounts(enrollment)
        self.assertEqual(len(tranches), 3)
        self.assertEqual(sum(tranches), Decimal("100"))

    def test_single_tranche_is_full_price(self):
        plan = make_plan(price=Decimal("25000"), max_installments=1)
        student = make_student(email="s@b.com")
        enrollment = Enrollment.objects.create(student=student, plan=plan)
        self.assertEqual(compute_tranche_amounts(enrollment), [Decimal("25000")])

    def test_next_tranche_state_advances_and_stops(self):
        plan = make_plan(price=Decimal("30000"), max_installments=3)
        student = make_student(email="n@b.com")
        enrollment = Enrollment.objects.create(student=student, plan=plan)
        idx, amount = next_tranche_state(enrollment)
        self.assertEqual(idx, 1)
        self.assertEqual(amount, (Decimal("30000") * Decimal("0.34")).quantize(Decimal("0.01")))
        # Simulate paying tranche 1
        p1 = Payment.objects.create(enrollment=enrollment, provider="kkiapay",
                                    amount=amount, tranche_index=1)
        mark_payment_success(p1)
        idx, _ = next_tranche_state(enrollment.refresh_status() or enrollment)
        self.assertEqual(idx, 2)


class PaidRatioUnlockTests(TestCase):
    def setUp(self):
        self.plan = make_plan(price=Decimal("30000"), notion_count=9, max_installments=3)
        self.student = make_student(email="u@b.com")
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        for i in range(1, 10):
            Notion.objects.create(title=f"N{i}", level="A1", order=i)

    def test_partial_payment_unlocks_floor_ratio(self):
        p = Payment.objects.create(enrollment=self.enrollment, provider="kkiapay",
                                   amount=Decimal("10200"), tranche_index=1)
        enrollment = mark_payment_success(p)
        # 10200/30000 = 0.34 -> floor(0.34*9) = 3 notions
        self.assertEqual(enrollment.paid_notion_count(), 3)
        self.assertEqual(paid_notions_available_for(self.student), 3)
        # Window opened on first 3 notions
        self.assertEqual(
            StudentProgress.objects.filter(student=self.student, status="in_progress").count(), 3)
        self.assertTrue(self.student.is_active)  # activated by first payment

    def test_full_payment_unlocks_all_and_marks_paid(self):
        for i, amt in enumerate([Decimal("10200"), Decimal("9900"), Decimal("9900")], start=1):
            p = Payment.objects.create(enrollment=self.enrollment, provider="flutterwave",
                                       amount=amt, tranche_index=i)
            enrollment = mark_payment_success(p)
        self.assertEqual(enrollment.status, "paid")
        self.assertEqual(enrollment.paid_notion_count(), 9)

    def test_admin_student_unlimited(self):
        admin_student = make_student(email="admin-made@b.com")
        self.assertIsNone(paid_notions_available_for(admin_student))

    def test_idempotent_double_webhook(self):
        p = Payment.objects.create(enrollment=self.enrollment, provider="kkiapay",
                                   amount=Decimal("10200"), tranche_index=1)
        mark_payment_success(p, provider_ref="TX1")
        mark_payment_success(p, provider_ref="TX1")  # webhook replayed
        self.assertEqual(self.enrollment.refresh_status() or
                         Enrollment.objects.get(pk=self.enrollment.pk).total_paid,
                         Decimal("10200"))


class WebhookSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.plan = make_plan()
        self.student = make_student(email="w@b.com", active=False)
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        self.payment = Payment.objects.create(
            enrollment=self.enrollment, provider="flutterwave",
            amount=Decimal("10200"), tranche_index=1)

    def _follow_session(self):
        """Give the client the signup-session marker AND persist it across
        requests (each request otherwise pops/consumes the session)."""
        self.client.session["pending_enrollment_id"] = self.enrollment.pk
        self.client.session.save()

    @patch("payments.providers.kkiapay_initiate_payment",
           return_value=("https://app.kkiapay.me/api/tpayment", {"apikey": "k"}))
    def test_checkout_amount_not_taken_from_post(self, minit):
        """POSTing a fake amount must not change what we bill.

        Use an authenticated student: the one-shot signup session marker is
        consumed by the first checkout POST, so a Client-based second POST
        would be rejected for ownership reasons, not billing ones.
        """
        self.student.is_active = True
        self.student.save(update_fields=["is_active"])
        self.client.login(username=self.student.username, password="pass12345")
        resp = self.client.post(
            reverse("payments:checkout", args=[self.enrollment.pk]),
            {"provider": "kkiapay", "amount": "1"})
        self.assertEqual(resp.status_code, 200)  # kkiapay redirect form page
        p = Payment.objects.filter(enrollment=self.enrollment).first()
        self.assertNotEqual(p.amount, Decimal("1"))

    def test_flutterwave_webhook_rejects_missing_hash(self):
        resp = self.client.post(reverse("payments:flutterwave_webhook"),
                                data={"data": {"event": "charge.completed"}},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "initiated")

    @patch("payments.providers.flutterwave_verify", return_value=None)
    def test_flutterwave_webhook_forged_hash_unverified_api(self, m):
        with self.settings(FLUTTERWAVE_SECRET_HASH="abc"):
            resp = self.client.post(
                reverse("payments:flutterwave_webhook"),
                data={"data": {"event": "charge.completed", "status": "successful",
                               "tx_ref": f"flw-{self.payment.id}-deadbeef"}},
                content_type="application/json",
                headers={"verif-hash": "WRONG"})
            self.assertEqual(resp.status_code, 403)

    @patch("payments.providers.flutterwave_verify")
    def test_flutterwave_callback_unverified_fails_tranche(self, mverify):
        mverify.return_value = None
        url = reverse("payments:flutterwave_callback", args=[self.payment.pk])
        self._follow_session()
        resp = self.client.get(url + f"?tx_ref=flw-{self.payment.id}-zzz")
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("payments:checkout",
                                           args=[self.enrollment.pk]),
                             fetch_redirect_response=False)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "failed")

    @patch("payments.providers.verify_kkiapay_payment", return_value=True)
    def test_kkiapay_callback_wrong_data_token_rejected(self, mverify):
        url = reverse("payments:kkiapay_callback", args=[self.payment.pk])
        self._follow_session()
        resp = self.client.post(url, {"data": "NOT-OUR-TOKEN", "id": "123"})
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "failed")

    @patch("payments.providers.verify_kkiapay_payment", return_value=True)
    def test_kkiapay_callback_valid_token_confirms(self, mverify):
        url = reverse("payments:kkiapay_callback", args=[self.payment.pk])
        self._follow_session()
        resp = self.client.post(url, {"data": self.payment.verification_token, "id": "KK1"})
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("payments:success", args=[self.enrollment.pk]),
                             fetch_redirect_response=False)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "success")
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)


class AccessGateTests(TestCase):
    def setUp(self):
        self.plan = make_plan(price=Decimal("30000"), notion_count=9)
        self.student = make_student(email="g@b.com")
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        self.n1 = Notion.objects.create(title="N1", level="A1", order=1)
        self.n5 = Notion.objects.create(title="N5", level="A1", order=5)
        self.client = Client()

    def test_locked_beyond_paid_window(self):
        p = Payment.objects.create(enrollment=self.enrollment, provider="kkiapay",
                                   amount=Decimal("10200"), tranche_index=1)
        mark_payment_success(p)  # unlocks window of 3
        self.client.force_login(self.student)
        resp = self.client.get(reverse("courses:notion_detail", args=[self.n5.pk]))
        self.assertEqual(resp.status_code, 403)  # order 5 > paid window 3

    def test_within_window_first_notion_open(self):
        p = Payment.objects.create(enrollment=self.enrollment, provider="kkiapay",
                                   amount=Decimal("10200"), tranche_index=1)
        mark_payment_success(p)
        self.client.force_login(self.student)
        resp = self.client.get(reverse("courses:notion_detail", args=[self.n1.pk]))
        self.assertEqual(resp.status_code, 200)


class HousekeepingTests(TestCase):
    def test_abandoned_signup_cancelled(self):
        from datetime import timedelta
        from django.utils import timezone
        plan = make_plan()
        student = make_student(email="old@b.com", active=False)
        enrollment = Enrollment.objects.create(student=student, plan=plan)
        Enrollment.objects.filter(pk=enrollment.pk).update(
            created_at=timezone.now() - timedelta(days=8))
        count = cancel_abandoned_enrollments()
        self.assertEqual(count, 1)
        self.assertFalse(CustomUser.objects.filter(pk=student.pk).exists())

    def test_partially_paid_never_cancelled(self):
        from datetime import timedelta
        from django.utils import timezone
        plan = make_plan()
        student = make_student(email="half@b.com")
        enrollment = Enrollment.objects.create(student=student, plan=plan)
        p = Payment.objects.create(enrollment=enrollment, provider="kkiapay",
                                   amount=Decimal("5000"), tranche_index=1)
        mark_payment_success(p)
        Enrollment.objects.filter(pk=enrollment.pk).update(
            created_at=timezone.now() - timedelta(days=30))
        count = cancel_abandoned_enrollments()
        self.assertEqual(count, 0)
        self.assertTrue(CustomUser.objects.filter(pk=student.pk).exists())

    @patch("payments.providers.flutterwave_verify")
    def test_recheck_confirms_lost_webhook(self, mverify):
        from datetime import timedelta
        from django.utils import timezone
        from .services import recheck_pending_payments
        plan = make_plan()
        student = make_student(email="lost@b.com", active=False)
        enrollment = Enrollment.objects.create(student=student, plan=plan)
        p = Payment.objects.create(enrollment=enrollment, provider="flutterwave",
                                   amount=Decimal("10200"), tranche_index=1)
        Payment.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(hours=2))
        mverify.return_value = p  # provider says: actually paid!
        summary = recheck_pending_payments(max_age_minutes=30)
        self.assertEqual(summary["confirmed"], 1)
        p.refresh_from_db()
        self.assertEqual(p.status, "success")
