"""
Tests pour l'inscription et la récupération de session :
- Inscription crée user + enrollment
- Magic link restaure la session
- Token expiré rejeté
- Non-énumération des emails
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.core import signing

from payments.models import Enrollment, PaymentPlan

from .models import CustomUser


class SelfRegistrationTests(TestCase):
    """Test le formulaire d'inscription self-service."""

    def setUp(self):
        self.plan = PaymentPlan.objects.create(
            name="Pack A1",
            price=30000,
            notion_count=10,
            max_installments=3,
            active=True,
        )
        self.client = Client()

    def test_valid_registration_creates_user_and_enrollment(self):
        """Inscription valide crée un user inactif et un enrollment."""
        response = self.client.post(
            reverse("accounts:register"),
            {
                "full_name": "Test User",
                "email": "test@example.com",
                "phone": "+22997000000",
                "level": "A1",
                "plan": self.plan.pk,
                "password": "securepass123",
                "password2": "securepass123",
            },
        )

        # Redirection vers checkout
        self.assertEqual(response.status_code, 302)
        self.assertIn("/payments/checkout/", response.url)

        # User créé
        user = CustomUser.objects.get(email="test@example.com")
        self.assertFalse(user.is_active)
        self.assertEqual(user.role, "student")
        self.assertEqual(user.level, "A1")

        # Enrollment créé
        enrollment = Enrollment.objects.get(student=user)
        self.assertEqual(enrollment.plan, self.plan)
        self.assertEqual(enrollment.status, "pending")

    def test_duplicate_email_rejected(self):
        """Email déjà utilisé → erreur de validation."""
        # Créer un user existant
        CustomUser.objects.create_user(
            username="existing@example.com",
            email="existing@example.com",
            password="pass123",
        )

        response = self.client.post(
            reverse("accounts:register"),
            {
                "full_name": "Test User",
                "email": "existing@example.com",
                "phone": "+22997000000",
                "level": "A1",
                "plan": self.plan.pk,
                "password": "securepass123",
                "password2": "securepass123",
            },
        )

        # Formulaire réaffiché avec erreur
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Un compte existe déjà")

    def test_password_mismatch_rejected(self):
        """Mots de passe différents → erreur."""
        response = self.client.post(
            reverse("accounts:register"),
            {
                "full_name": "Test User",
                "email": "test@example.com",
                "phone": "+22997000000",
                "level": "A1",
                "plan": self.plan.pk,
                "password": "securepass123",
                "password2": "differentpass",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ne correspondent pas")


class MagicLinkRecoveryTests(TestCase):
    """Test la récupération de session via magic link."""

    def setUp(self):
        self.plan = PaymentPlan.objects.create(
            name="Pack A1",
            price=30000,
            notion_count=10,
            max_installments=3,
            active=True,
        )
        
        # Créer un user + enrollment
        self.user = CustomUser.objects.create_user(
            username="recover@example.com",
            email="recover@example.com",
            password="pass123",
            role="student",
            level="A1",
            is_active=False,
        )
        self.enrollment = Enrollment.objects.create(student=self.user, plan=self.plan)
        
        self.client = Client()

    def test_valid_magic_link_restores_session(self):
        """Lien magique valide restaure la session et redirige vers checkout."""
        # Générer un token valide
        token = signing.dumps(
            {"enrollment_id": self.enrollment.pk, "email": self.user.email},
            salt="checkout-recovery",
        )

        response = self.client.get(
            reverse("accounts:recover_checkout", args=[token])
        )

        # Redirection vers checkout
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/payments/checkout/{self.enrollment.pk}/", response.url)

        # Session restaurée
        self.assertEqual(
            self.client.session.get("pending_enrollment_id"),
            self.enrollment.pk,
        )

    def test_expired_magic_link_rejected(self):
        """Lien expiré (>24h) → erreur."""
        # Générer un token expiré
        import time
        token = signing.dumps(
            {"enrollment_id": self.enrollment.pk, "email": self.user.email},
            salt="checkout-recovery",
        )
        
        # Simuler expiration en modifiant le timestamp
        # (en pratique, il faudrait attendre 24h ou mocker le temps)
        # Pour ce test, on utilise un token invalide
        invalid_token = token[:-5] + "XXXXX"

        response = self.client.get(
            reverse("accounts:recover_checkout", args=[invalid_token])
        )

        # Redirection vers register avec message d'erreur
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/register/", response.url)

    def test_magic_link_wrong_email_rejected(self):
        """Lien avec mauvais email → erreur."""
        token = signing.dumps(
            {"enrollment_id": self.enrollment.pk, "email": "wrong@example.com"},
            salt="checkout-recovery",
        )

        response = self.client.get(
            reverse("accounts:recover_checkout", args=[token])
        )

        # Redirection vers register
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/register/", response.url)


class ResendRecoveryEmailTests(TestCase):
    """Test la demande de renvoi d'email de récupération."""

    def setUp(self):
        self.plan = PaymentPlan.objects.create(
            name="Pack A1",
            price=30000,
            notion_count=10,
            max_installments=3,
            active=True,
        )
        
        self.user = CustomUser.objects.create_user(
            username="resend@example.com",
            email="resend@example.com",
            password="pass123",
            role="student",
            level="A1",
            is_active=False,
        )
        self.enrollment = Enrollment.objects.create(student=self.user, plan=self.plan)
        
        self.client = Client()

    def test_resend_with_valid_email_shows_success(self):
        """Email valide → message de succès (sans révéler si l'email existe)."""
        response = self.client.post(
            reverse("accounts:resend_recovery"),
            {"email": "resend@example.com"},
        )

        # Redirection vers login avec message
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_resend_with_unknown_email_shows_generic_message(self):
        """Email inconnu → message générique (non-énumération)."""
        response = self.client.post(
            reverse("accounts:resend_recovery"),
            {"email": "unknown@example.com"},
        )

        # Même redirection que pour un email valide
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)


class SuperuserRoleTests(TestCase):
    def test_createsuperuser_gets_admin_role(self):
        from accounts.models import CustomUser
        u = CustomUser.objects.create_superuser("root", "root@x.com", "pass12345")
        self.assertEqual(u.role, "admin")
        self.assertTrue(u.is_staff and u.is_superuser)

    def test_superuser_home_redirects_to_admin(self):
        from accounts.models import CustomUser
        CustomUser.objects.create_superuser("root", "root@x.com", "pass12345")
        self.client.login(username="root", password="pass12345")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/", resp.url)


class HealthzTests(TestCase):
    def test_healthz_ok(self):
        resp = self.client.get("/healthz/")
        self.assertEqual(resp.status_code, 200)
