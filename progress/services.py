from django.conf import settings
from django.utils import timezone

from .models import StudentProgress


def register_attempt(quiz_attempt):
    """
    Called from quizzes/views.py right after a QuizAttempt is graded.
    Updates StudentProgress.best_score, and if the attempt passes the
    fixed threshold (settings.QUIZ_PASS_THRESHOLD), marks the notion
    completed and unlocks the next one in the level.
    """
    student = quiz_attempt.student
    notion = quiz_attempt.quiz.notion
    threshold = settings.QUIZ_PASS_THRESHOLD

    progress, _ = StudentProgress.objects.get_or_create(student=student, notion=notion)
    progress.best_score = max(quiz_attempt.score, progress.best_score or 0)

    if quiz_attempt.score >= threshold and progress.status != "completed":
        progress.status = "completed"
        progress.completed_at = timezone.now()
        progress.save()
        _unlock_next(student, notion)
    else:
        progress.save()


def _unlock_next(student, notion):
    next_notion = notion.next()
    if next_notion is None:
        return
    next_progress, _ = StudentProgress.objects.get_or_create(student=student, notion=next_notion)
    if next_progress.status == "locked":
        next_progress.status = "in_progress"
        next_progress.unlocked_at = timezone.now()
        next_progress.save()
