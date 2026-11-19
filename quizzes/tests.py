"""
Tests pour la logique de quiz :
- Scoring correct
- Champ `passed` positionné
- Anti double-submit
- Déblocage séquentiel après validation
- Emails de félicitations
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.conf import settings

from accounts.models import CustomUser
from courses.models import Notion
from payments.models import Enrollment, Payment, PaymentPlan
from progress.models import StudentProgress

from .models import Quiz, Question, Choice, QuizAttempt


def make_plan(**kw):
    kw.setdefault("name", "Pack A1")
    kw.setdefault("price", Decimal("30000"))
    kw.setdefault("notion_count", 9)
    kw.setdefault("max_installments", 3)
    return PaymentPlan.objects.create(**kw)


def make_student(email="quiz@test.com", level="A1", active=True):
    return CustomUser.objects.create_user(
        username=email,
        email=email,
        password="pass12345",
        role="student",
        level=level,
        is_active=active,
        phone_number="+22997000000",
    )


class QuizScoringTests(TestCase):
    """Test que le scoring et le champ `passed` fonctionnent correctement."""

    def setUp(self):
        self.plan = make_plan()
        self.student = make_student()
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        
        self.notion1 = Notion.objects.create(title="N1", level="A1", order=1)
        self.notion2 = Notion.objects.create(title="N2", level="A1", order=2)
        
        # Payer pour débloquer la première notion
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        self.quiz = Quiz.objects.create(notion=self.notion1, title="Quiz N1")
        
        # 4 questions, chaque bonne réponse = 25%
        for i in range(4):
            q = Question.objects.create(quiz=self.quiz, text=f"Q{i+1}", order=i+1)
            Choice.objects.create(question=q, text="Wrong 1", is_correct=False)
            Choice.objects.create(question=q, text="Wrong 2", is_correct=False)
            Choice.objects.create(question=q, text="Correct", is_correct=True)

        self.client = Client()
        self.client.force_login(self.student)

    def test_perfect_score_passes(self):
        """4/4 = 100% → passed=True"""
        post_data = {}
        for q in self.quiz.questions.all():
            correct = q.choices.filter(is_correct=True).first()
            post_data[f"question_{q.id}"] = correct.id

        response = self.client.post(
            reverse("quizzes:quiz_take", args=[self.quiz.pk]),
            post_data,
        )

        self.assertEqual(response.status_code, 200)
        
        attempt = QuizAttempt.objects.filter(student=self.student, quiz=self.quiz).first()
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.score, 100)
        self.assertTrue(attempt.passed)

    def test_75_percent_passes(self):
        """3/4 = 75% → passed=True (seuil = 70%)"""
        post_data = {}
        questions = list(self.quiz.questions.all())
        
        for i, q in enumerate(questions[:3]):
            correct = q.choices.filter(is_correct=True).first()
            post_data[f"question_{q.id}"] = correct.id
        
        wrong = questions[3].choices.filter(is_correct=False).first()
        post_data[f"question_{questions[3].id}"] = wrong.id

        response = self.client.post(
            reverse("quizzes:quiz_take", args=[self.quiz.pk]),
            post_data,
        )

        attempt = QuizAttempt.objects.filter(student=self.student, quiz=self.quiz).first()
        self.assertEqual(attempt.score, 75)
        self.assertTrue(attempt.passed)

    def test_50_percent_fails(self):
        """2/4 = 50% → passed=False"""
        post_data = {}
        questions = list(self.quiz.questions.all())
        
        for i, q in enumerate(questions[:2]):
            correct = q.choices.filter(is_correct=True).first()
            post_data[f"question_{q.id}"] = correct.id
        
        for q in questions[2:]:
            wrong = q.choices.filter(is_correct=False).first()
            post_data[f"question_{q.id}"] = wrong.id

        response = self.client.post(
            reverse("quizzes:quiz_take", args=[self.quiz.pk]),
            post_data,
        )

        attempt = QuizAttempt.objects.filter(student=self.student, quiz=self.quiz).first()
        self.assertEqual(attempt.score, 50)
        self.assertFalse(attempt.passed)

    def test_zero_score_fails(self):
        """0/4 = 0% → passed=False"""
        post_data = {}
        for q in self.quiz.questions.all():
            wrong = q.choices.filter(is_correct=False).first()
            post_data[f"question_{q.id}"] = wrong.id

        self.client.post(
            reverse("quizzes:quiz_take", args=[self.quiz.pk]),
            post_data,
        )

        attempt = QuizAttempt.objects.filter(student=self.student, quiz=self.quiz).first()
        self.assertEqual(attempt.score, 0)
        self.assertFalse(attempt.passed)

    def test_no_answers_zero_score(self):
        """Aucune réponse → 0% → passed=False"""
        self.client.post(
            reverse("quizzes:quiz_take", args=[self.quiz.pk]),
            {},
        )

        attempt = QuizAttempt.objects.filter(student=self.student, quiz=self.quiz).first()
        self.assertEqual(attempt.score, 0)
        self.assertFalse(attempt.passed)


class QuizUnlockTests(TestCase):
    """Test que le quiz débloque correctement la notion suivante."""

    def setUp(self):
        self.plan = make_plan()
        self.student = make_student()
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        
        # IMPORTANT: Créer les notions AVANT le paiement
        self.notion1 = Notion.objects.create(title="N1", level="A1", order=1)
        self.notion2 = Notion.objects.create(title="N2", level="A1", order=2)
        self.notion3 = Notion.objects.create(title="N3", level="A1", order=3)
        
        # Payer pour débloquer 3 notions
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)
        
        self.quiz1 = Quiz.objects.create(notion=self.notion1, title="Quiz N1")
        
        # 4 questions pour le quiz 1
        for i in range(4):
            q = Question.objects.create(quiz=self.quiz1, text=f"Q{i+1}", order=i+1)
            Choice.objects.create(question=q, text="Wrong", is_correct=False)
            Choice.objects.create(question=q, text="Correct", is_correct=True)

        self.client = Client()
        self.client.force_login(self.student)

    def test_passing_quiz_unlocks_next_notion(self):
        """Valider le quiz de N1 débloque N2."""
        # Vérifier l'état initial
        progress1 = StudentProgress.objects.get(student=self.student, notion=self.notion1)
        self.assertEqual(progress1.status, "in_progress")
        
        # N2 existe mais est locked (créée par _sync_unlocked_window)
        progress2 = StudentProgress.objects.get(student=self.student, notion=self.notion2)
        self.assertEqual(progress2.status, "locked")

        # Passer le quiz avec succès (100%)
        post_data = {}
        for q in self.quiz1.questions.all():
            correct = q.choices.filter(is_correct=True).first()
            post_data[f"question_{q.id}"] = correct.id

        self.client.post(
            reverse("quizzes:quiz_take", args=[self.quiz1.pk]),
            post_data,
        )

        # Vérifier que N1 est maintenant completed
        progress1.refresh_from_db()
        self.assertEqual(progress1.status, "completed")
        self.assertIsNotNone(progress1.completed_at)

        # Vérifier que N2 est maintenant in_progress
        progress2.refresh_from_db()
        self.assertEqual(progress2.status, "in_progress")
        self.assertIsNotNone(progress2.unlocked_at)

        # N3 devrait rester locked
        progress3 = StudentProgress.objects.get(student=self.student, notion=self.notion3)
        self.assertEqual(progress3.status, "locked")

    def test_failing_quiz_does_not_unlock(self):
        """Échouer le quiz ne débloque pas la notion suivante."""
        # Vérifier l'état initial
        progress2 = StudentProgress.objects.get(student=self.student, notion=self.notion2)
        self.assertEqual(progress2.status, "locked")

        # Passer le quiz avec échec (0%)
        post_data = {}
        for q in self.quiz1.questions.all():
            wrong = q.choices.filter(is_correct=False).first()
            post_data[f"question_{q.id}"] = wrong.id

        self.client.post(
            reverse("quizzes:quiz_take", args=[self.quiz1.pk]),
            post_data,
        )

        # N1 reste in_progress
        progress1 = StudentProgress.objects.get(student=self.student, notion=self.notion1)
        self.assertEqual(progress1.status, "in_progress")

        # N2 reste locked (pas débloquée)
        progress2.refresh_from_db()
        self.assertEqual(progress2.status, "locked")


class QuizAntiDoubleSubmitTests(TestCase):
    """Test la protection anti double-clic."""

    def setUp(self):
        self.plan = make_plan()
        self.student = make_student()
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        
        self.notion1 = Notion.objects.create(title="N1", level="A1", order=1)
        
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        self.quiz = Quiz.objects.create(notion=self.notion1)
        
        q = Question.objects.create(quiz=self.quiz, text="Q1", order=1)
        self.correct = Choice.objects.create(question=q, text="Correct", is_correct=True)

        self.client = Client()
        self.client.force_login(self.student)

    def test_double_submit_blocked_within_3_seconds(self):
        """Double-clic rapide → un seul QuizAttempt créé."""
        post_data = {f"question_{q.id}": self.correct.id for q in self.quiz.questions.all()}
        url = reverse("quizzes:quiz_take", args=[self.quiz.pk])

        self.client.post(url, post_data)
        self.client.post(url, post_data)

        attempts = QuizAttempt.objects.filter(student=self.student, quiz=self.quiz)
        self.assertEqual(attempts.count(), 1)

    def test_resubmit_after_4_seconds_allowed(self):
        """Resoumettre après 4 secondes → nouvel attempt créé."""
        post_data = {f"question_{q.id}": self.correct.id for q in self.quiz.questions.all()}
        url = reverse("quizzes:quiz_take", args=[self.quiz.pk])

        self.client.post(url, post_data)
        
        import time
        time.sleep(4)
        
        self.client.post(url, post_data)

        attempts = QuizAttempt.objects.filter(student=self.student, quiz=self.quiz)
        self.assertEqual(attempts.count(), 2)


class QuizAccessControlTests(TestCase):
    """Test que l'accès au quiz respecte les permissions."""

    def setUp(self):
        self.plan = make_plan()
        self.student = make_student()
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)

        self.notion1 = Notion.objects.create(title="N1", level="A1", order=1)
        self.notion2 = Notion.objects.create(title="N2", level="A1", order=2)
        
        self.quiz1 = Quiz.objects.create(notion=self.notion1)
        self.quiz2 = Quiz.objects.create(notion=self.notion2)

        self.client = Client()

    def test_anonymous_cannot_access_quiz(self):
        """Utilisateur anonyme redirigé vers login."""
        response = self.client.get(reverse("quizzes:quiz_take", args=[self.quiz1.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_student_cannot_access_locked_quiz(self):
        """Étudiant ne peut pas accéder au quiz d'une notion non débloquée."""
        self.client.force_login(self.student)
        
        response = self.client.get(reverse("quizzes:quiz_take", args=[self.quiz2.pk]))
        self.assertEqual(response.status_code, 403)

    def test_student_can_access_unlocked_quiz(self):
        """Étudiant peut accéder au quiz d'une notion débloquée."""
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        self.client.force_login(self.student)
        response = self.client.get(reverse("quizzes:quiz_take", args=[self.quiz1.pk]))
        self.assertEqual(response.status_code, 200)


class QuizEmailTests(TestCase):
    """Test que les emails sont envoyés correctement."""

    def setUp(self):
        self.plan = make_plan()
        self.student = make_student(email="email@test.com")
        self.enrollment = Enrollment.objects.create(student=self.student, plan=self.plan)
        
        self.notion1 = Notion.objects.create(title="N1", level="A1", order=1)
        
        p = Payment.objects.create(
            enrollment=self.enrollment,
            provider="kkiapay",
            amount=Decimal("10200"),
            tranche_index=1,
        )
        from payments.services import mark_payment_success
        mark_payment_success(p)

        self.quiz = Quiz.objects.create(notion=self.notion1)
        
        q = Question.objects.create(quiz=self.quiz, text="Q1", order=1)
        self.correct = Choice.objects.create(question=q, text="Correct", is_correct=True)

        self.client = Client()
        self.client.force_login(self.student)

    def test_email_sent_on_pass(self):
        """Email envoyé quand le quiz est réussi."""
        post_data = {f"question_{q.id}": self.correct.id for q in self.quiz.questions.all()}
        
        # captureOnCommitCallbacks exécute les callbacks on_commit
        with patch("accounts.services.send_quiz_completion_email") as mock_email:
            mock_email.return_value = True
            
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(
                    reverse("quizzes:quiz_take", args=[self.quiz.pk]),
                    post_data,
                )

            # Email appelé
            mock_email.assert_called_once()
            args = mock_email.call_args[0]
            self.assertEqual(args[0], self.student)  # student
            self.assertEqual(args[1], self.notion1)  # notion
            self.assertEqual(args[2], 100)  # score

    def test_no_email_on_fail(self):
        """Aucun email envoyé quand le quiz est échoué."""
        post_data = {}
        for q in self.quiz.questions.all():
            wrong = q.choices.filter(is_correct=False).first()
            if wrong:
                post_data[f"question_{q.id}"] = wrong.id

        with patch("accounts.services.send_quiz_completion_email") as mock_email:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(
                    reverse("quizzes:quiz_take", args=[self.quiz.pk]),
                    post_data,
                )

            # Email non appelé
            mock_email.assert_not_called()
