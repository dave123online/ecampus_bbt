"""
Tests pour le dashboard et la logique de déblocage :
- Dashboard affiche le bon statut
- Pourcentage de progression correct
- Bloc paiement affiché pour les étudiants payants
"""
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import CustomUser
from courses.models import Notion
from payments.models import Enrollment, Payment, PaymentPlan
from progress.models import StudentProgress


def make_plan(**kw):
    kw.setdefault("name", "Pack A1")
    kw.setdefault("price", Decimal("30000"))
    kw.setdefault("notion_count", 9)
    kw.setdefault("max_installments", 3)
    return PaymentPlan.objects.create(**kw)


def make_student(email="progress@test.com", level="A1", active=True):
    return CustomUser.objects.create_user(
        username=email,
        email=email,
        password="pass12345",
        role="student",
        level=level,
        is_active=active,
        phone_number="+22997000000",
    )


class DashboardDisplayTests(TestCase):
    """Test l'affichage du dashboard."""

    def setUp(self):
        self.plan = make_plan()
        self.student = make_student()
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        
        # Créer 5 notions
        for i in range(1, 6):
            Notion.objects.create(title=f"N{i}", level="A1", order=i)

        self.client = Client()
        self.client.force_login(self.student)

    def test_dashboard_shows_payment_block_for_paid_student(self):
        """Étudiant payant voit le bloc paiement."""
        # Payer tranche 1
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        response = self.client.get(reverse("progress:dashboard"))
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Partiellement payé")
        self.assertContains(response, "Payer la tranche suivante")

    def test_dashboard_no_payment_block_for_admin_student(self):
        """Étudiant admin (sans enrollment) ne voit pas de bloc paiement."""
        admin_student = make_student(email="admin@test.com")
        self.client.force_login(admin_student)

        response = self.client.get(reverse("progress:dashboard"))
        
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Partiellement payé")
        self.assertNotContains(response, "Payer la tranche suivante")

    def test_dashboard_shows_correct_progression(self):
        """Dashboard affiche le bon pourcentage de progression."""
        # Payer tranche 1
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        # Valider la notion 1
        n1 = Notion.objects.get(order=1)
        progress1 = StudentProgress.objects.get(student=self.student, notion=n1)
        progress1.status = "completed"
        progress1.save()

        response = self.client.get(reverse("progress:dashboard"))
        
        # 1/5 = 20%
        self.assertContains(response, "20%")


class UnlockLogicTests(TestCase):
    """Test la logique de déblocage des notions."""

    def setUp(self):
        self.plan = make_plan()
        self.student = make_student()
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        
        # IMPORTANT: Créer les notions AVANT le paiement
        for i in range(1, 6):
            Notion.objects.create(title=f"N{i}", level="A1", order=i)
        
        # Payer pour débloquer 3 notions
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

    def test_only_first_notion_unlocked_initially(self):
        """Après paiement, seule la notion 1 est in_progress."""
        n1 = Notion.objects.get(order=1)
        progress1 = StudentProgress.objects.get(student=self.student, notion=n1)
        self.assertEqual(progress1.status, "in_progress")

        # Notions 2-3 dans la fenêtre payée mais locked (séquentiel)
        for order in [2, 3]:
            n = Notion.objects.get(order=order)
            progress = StudentProgress.objects.filter(student=self.student, notion=n).first()
            if progress:
                self.assertEqual(progress.status, "locked")

        # Notions 4-5 hors fenêtre payée
        for order in [4, 5]:
            n = Notion.objects.get(order=order)
            progress = StudentProgress.objects.filter(student=self.student, notion=n).first()
            # Pas de progress créé ou locked
            if progress:
                self.assertEqual(progress.status, "locked")

    def test_unlock_next_respects_paid_window(self):
        """_unlock_next ne débloque pas au-delà de la fenêtre payée."""
        from progress.services import _unlock_next
        
        n1 = Notion.objects.get(order=1)
        n2 = Notion.objects.get(order=2)
        n3 = Notion.objects.get(order=3)
        n4 = Notion.objects.get(order=4)

        # Valider N1
        progress1 = StudentProgress.objects.get(student=self.student, notion=n1)
        progress1.status = "completed"
        progress1.save()

        # Débloquer N2
        _unlock_next(self.student, n1)
        progress2 = StudentProgress.objects.get(student=self.student, notion=n2)
        self.assertEqual(progress2.status, "in_progress")

        # Valider N2
        progress2.status = "completed"
        progress2.save()

        # Débloquer N3
        _unlock_next(self.student, n2)
        progress3 = StudentProgress.objects.get(student=self.student, notion=n3)
        self.assertEqual(progress3.status, "in_progress")

        # Valider N3
        progress3.status = "completed"
        progress3.save()

        # Tenter de débloquer N4 (hors fenêtre payée)
        _unlock_next(self.student, n3)
        
        # N4 ne doit pas être débloquée
        progress4 = StudentProgress.objects.filter(student=self.student, notion=n4).first()
        if progress4:
            self.assertNotEqual(progress4.status, "in_progress")
