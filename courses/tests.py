"""
Tests pour les permissions d'accès aux notions et fichiers :
- Accès séquentiel respecté
- Fenêtre de paiement respectée
- Protection des PDF/audio
- Étudiants admin (sans enrollment) = accès illimité
"""
from decimal import Decimal

import shutil
import tempfile

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from accounts.models import CustomUser
from payments.models import Enrollment, Payment, PaymentPlan
from progress.models import StudentProgress

from .models import Notion, PDFResource, AudioResource


def make_plan(**kw):
    kw.setdefault("name", "Pack A1")
    kw.setdefault("price", Decimal("30000"))
    kw.setdefault("notion_count", 9)
    kw.setdefault("max_installments", 3)
    return PaymentPlan.objects.create(**kw)


def make_student(email="course@test.com", level="A1", active=True):
    return CustomUser.objects.create_user(
        username=email,
        email=email,
        password="pass12345",
        role="student",
        level=level,
        is_active=active,
        phone_number="+22997000000",
    )


class NotionAccessSequentialTests(TestCase):
    """Test que l'accès séquentiel est respecté."""

    def setUp(self):
        self.plan = make_plan()
        self.student = make_student()
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        
        # Créer 5 notions
        for i in range(1, 6):
            Notion.objects.create(title=f"N{i}", level="A1", order=i)

        self.client = Client()
        self.client.force_login(self.student)

    def test_no_payment_only_first_notion_accessible(self):
        """Sans paiement, seule la notion 1 est accessible."""
        # Payer tranche 1 → 3 notions dans la fenêtre payée
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        # Notion 1 accessible
        n1 = Notion.objects.get(order=1)
        response = self.client.get(reverse("courses:notion_detail", args=[n1.pk]))
        self.assertEqual(response.status_code, 200)

        # Notion 2 locked (pas encore validé quiz 1)
        n2 = Notion.objects.get(order=2)
        response = self.client.get(reverse("courses:notion_detail", args=[n2.pk]))
        self.assertEqual(response.status_code, 403)

        # Notion 4 locked (hors fenêtre payée)
        n4 = Notion.objects.get(order=4)
        response = self.client.get(reverse("courses:notion_detail", args=[n4.pk]))
        self.assertEqual(response.status_code, 403)

    def test_after_quiz_validation_next_notion_unlocked(self):
        """Après validation du quiz, la notion suivante est débloquée."""
        # Payer tranche 1
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        n1 = Notion.objects.get(order=1)
        n2 = Notion.objects.get(order=2)

        # Simuler validation du quiz de N1
        progress1 = StudentProgress.objects.get(student=self.student, notion=n1)
        progress1.status = "completed"
        progress1.save()

        # Débloquer N2
        from progress.services import _unlock_next
        _unlock_next(self.student, n1)

        # N2 maintenant accessible
        response = self.client.get(reverse("courses:notion_detail", args=[n2.pk]))
        self.assertEqual(response.status_code, 200)


class NotionAccessPaidWindowTests(TestCase):
    """Test que la fenêtre de paiement est respectée."""

    def setUp(self):
        self.plan = make_plan(notion_count=10)
        self.student = make_student()
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        
        # Créer 10 notions
        for i in range(1, 11):
            Notion.objects.create(title=f"N{i}", level="A1", order=i)

        self.client = Client()
        self.client.force_login(self.student)

    def test_partial_payment_limits_access(self):
        """Paiement partiel limite l'accès aux premières notions."""
        # Payer tranche 1 (34%) → 3 notions accessibles
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        # Notions 1-3 dans la fenêtre payée
        for order in [1, 2, 3]:
            n = Notion.objects.get(order=order)
            # Notion 1 accessible, 2-3 locked (séquentiel)
            response = self.client.get(reverse("courses:notion_detail", args=[n.pk]))
            if order == 1:
                self.assertEqual(response.status_code, 200)
            else:
                self.assertEqual(response.status_code, 403)

        # Notions 4-10 hors fenêtre payée → toutes locked
        for order in range(4, 11):
            n = Notion.objects.get(order=order)
            response = self.client.get(reverse("courses:notion_detail", args=[n.pk]))
            self.assertEqual(response.status_code, 403)


class AdminStudentAccessTests(TestCase):
    """Test que les étudiants créés par l'admin ont un accès illimité."""

    def setUp(self):
        # Étudiant créé par l'admin (pas d'Enrollment)
        self.admin_student = CustomUser.objects.create_user(
            username="admin@test.com",
            email="admin@test.com",
            password="pass12345",
            role="student",
            level="A1",
            is_active=True,
        )

        # Créer 5 notions
        for i in range(1, 6):
            Notion.objects.create(title=f"N{i}", level="A1", order=i)

        self.client = Client()
        self.client.force_login(self.admin_student)

    def test_admin_student_can_access_all_notions(self):
        """Étudiant admin peut accéder à toutes les notions de son niveau."""
        for order in range(1, 6):
            n = Notion.objects.get(order=order)
            response = self.client.get(reverse("courses:notion_detail", args=[n.pk]))
            self.assertEqual(response.status_code, 200)


_TEST_MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=_TEST_MEDIA)  # n'écrit jamais dans le vrai media/
class ProtectedFileAccessTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TEST_MEDIA, ignore_errors=True)

    """Test la protection des fichiers PDF et audio."""

    def setUp(self):
        self.plan = make_plan()
        self.student = make_student()
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        
        self.notion1 = Notion.objects.create(title="N1", level="A1", order=1)
        self.notion2 = Notion.objects.create(title="N2", level="A1", order=2)

        # Créer des fichiers (fictifs pour les tests)
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        self.pdf1 = PDFResource.objects.create(
            notion=self.notion1,
            file=SimpleUploadedFile("test1.pdf", b"PDF content", content_type="application/pdf"),
            title="PDF N1",
        )
        
        self.pdf2 = PDFResource.objects.create(
            notion=self.notion2,
            file=SimpleUploadedFile("test2.pdf", b"PDF content", content_type="application/pdf"),
            title="PDF N2",
        )

        self.client = Client()

    def test_anonymous_cannot_access_pdf(self):
        """Utilisateur anonyme redirigé vers login."""
        response = self.client.get(reverse("courses:serve_pdf", args=[self.pdf1.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_student_can_access_unlocked_pdf(self):
        """Étudiant peut accéder au PDF d'une notion débloquée."""
        # Payer pour débloquer N1
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        self.client.force_login(self.student)
        response = self.client.get(reverse("courses:serve_pdf", args=[self.pdf1.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_student_cannot_access_locked_pdf(self):
        """Étudiant ne peut pas accéder au PDF d'une notion non débloquée."""
        # Payer pour débloquer N1 uniquement
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        self.client.force_login(self.student)
        response = self.client.get(reverse("courses:serve_pdf", args=[self.pdf2.pk]))
        self.assertEqual(response.status_code, 404)  # 404 pour ne pas révéler l'existence
