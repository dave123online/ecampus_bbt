import logging
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from courses.permissions import notion_access_allowed
from progress.services import register_attempt
from .models import Choice, Quiz, QuizAnswer, QuizAttempt

logger = logging.getLogger(__name__)

# Minimum seconds between two quiz submissions (anti double-click)
MIN_SUBMIT_INTERVAL = 3


@login_required
def quiz_take(request, pk):
    quiz = get_object_or_404(Quiz.objects.select_related("notion"), pk=pk)
    
    # Same gate as the notion page itself.
    if not notion_access_allowed(request.user, quiz.notion):
        raise PermissionDenied("Ce quiz n'est pas accessible pour vous.")

    if request.method == "POST":
        # --- Anti double-submit guard ---
        recent_attempt = (
            QuizAttempt.objects
            .filter(student=request.user, quiz=quiz)
            .order_by("-attempted_at")
            .first()
        )

        if recent_attempt:
            elapsed = (timezone.now() - recent_attempt.attempted_at).total_seconds()
            if elapsed < MIN_SUBMIT_INTERVAL:
                logger.warning(
                    "Double-submit blocked: user=%s quiz=%s elapsed=%.1fs",
                    request.user.pk,
                    quiz.pk,
                    elapsed,
                )
                return render(
                    request,
                    "quizzes/quiz_result.html",
                    {"attempt": recent_attempt, "quiz": quiz},
                )

        # --- Grade the quiz ---
        questions = quiz.questions.all()
        correct_count = 0
        selected_choices = {}

        for question in questions:
            choice_id = request.POST.get(f"question_{question.id}")
            choice = Choice.objects.filter(pk=choice_id, question=question).first()
            selected_choices[question.id] = choice

            if choice and choice.is_correct:
                correct_count += 1

        total_questions = questions.count()
        score = round((correct_count / total_questions) * 100) if total_questions else 0
        passed = score >= settings.QUIZ_PASS_THRESHOLD

        with db_transaction.atomic():
            attempt = QuizAttempt.objects.create(
                student=request.user,
                quiz=quiz,
                score=score,
                passed=passed,
            )

            for question_id, choice in selected_choices.items():
                if choice:
                    QuizAnswer.objects.create(
                        attempt=attempt,
                        question_id=question_id,
                        selected_choice=choice,
                    )

            # Progress/unlock logic
            register_attempt(attempt)

            # Email after commit, only if passed
            if attempt.passed:
                from accounts.services import send_quiz_completion_email
                db_transaction.on_commit(
                    lambda: send_quiz_completion_email(
                        request.user,
                        quiz.notion,
                        score,
                    )
                )

        return render(
            request,
            "quizzes/quiz_result.html",
            {"attempt": attempt, "quiz": quiz},
        )

    return render(request, "quizzes/quiz_take.html", {"quiz": quiz})
