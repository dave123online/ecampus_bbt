from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models

from courses.models import Notion


class Quiz(models.Model):
    notion = models.OneToOneField(Notion, on_delete=models.CASCADE, related_name="quiz")
    title = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"Quiz - {self.notion}"


class Question(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order"]


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)


class QuizAttempt(models.Model):
    """
    Full history of every attempt — kept for admin analytics (Maya's
    requirement: see every attempt, not just the best one).

    NOTE on architecture: unlock logic used to live directly in this
    model's save() (see the original single-file models.py). It has been
    moved to progress.services.register_attempt(), called explicitly from
    quizzes/views.py after grading, to avoid a circular import between
    quizzes <-> progress and to keep the scoring/unlock logic testable
    independently of the ORM save cycle.
    """
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quiz_attempts")
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts")
    score = models.PositiveIntegerField(validators=[MinValueValidator(0), MaxValueValidator(100)])
    passed = models.BooleanField(default=False)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-attempted_at"]


class QuizAnswer(models.Model):
    """Individual answer within an attempt — lets admin review exactly what was selected."""
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_choice = models.ForeignKey(Choice, on_delete=models.CASCADE)
