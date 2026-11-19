from django.conf import settings
from django.utils import timezone

from .models import StudentProgress


def register_attempt(quiz_attempt):
    """
    Called from quizzes/views.py right after a QuizAttempt is graded.

    - Updates StudentProgress.best_score
    - Sets quiz_attempt.passed if needed
    - Marks notion completed if threshold is met
    - Unlocks next notion if paid/sequential rules allow it
    """
    student = quiz_attempt.student
    notion = quiz_attempt.quiz.notion
    threshold = settings.QUIZ_PASS_THRESHOLD

    passed = quiz_attempt.score >= threshold

    if passed and not quiz_attempt.passed:
        quiz_attempt.passed = True
        quiz_attempt.save(update_fields=["passed"])

    progress, _ = StudentProgress.objects.get_or_create(student=student, notion=notion)
    progress.best_score = max(quiz_attempt.score, progress.best_score or 0)

    if passed and progress.status != "completed":
        progress.status = "completed"
        progress.completed_at = timezone.now()
        progress.save()
        _unlock_next(student, notion)
    else:
        progress.save()


def _unlock_next(student, notion):
    """
    Unlock the next notion only if:
    - it exists
    - it is inside the paid window for self-registered students
    """
    next_notion = notion.next()

    if next_notion is None:
        return

    # Local import avoids circular imports at app load.
    from payments.services import paid_notions_available_for

    available = paid_notions_available_for(student)

    if available is not None and next_notion.order > available:
        return

    next_progress, _ = StudentProgress.objects.get_or_create(
        student=student,
        notion=next_notion,
    )

    if next_progress.status == "locked":
        next_progress.status = "in_progress"
        next_progress.unlocked_at = timezone.now()
        next_progress.save(update_fields=["status", "unlocked_at"])
