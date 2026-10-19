from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render, redirect

from courses.permissions import notion_access_allowed
from progress.services import register_attempt

from .models import Quiz, QuizAttempt, QuizAnswer, Choice


@login_required
def quiz_take(request, pk):
    quiz = get_object_or_404(Quiz.objects.select_related("notion"), pk=pk)

    # Same gate as the notion page itself: level match + paid quota +
    # sequential unlock. Prevents hitting quiz URLs directly to bypass it.
    if not notion_access_allowed(request.user, quiz.notion):
        raise PermissionDenied("Ce quiz n'est pas accessible pour vous.")

    if request.method == "POST":
        questions = quiz.questions.all()
        correct_count = 0
        selected_choices = {}

        for question in questions:
            choice_id = request.POST.get(f"question_{question.id}")
            choice = Choice.objects.filter(pk=choice_id, question=question).first()
            selected_choices[question.id] = choice
            if choice and choice.is_correct:
                correct_count += 1

        score = round((correct_count / questions.count()) * 100) if questions.count() else 0

        attempt = QuizAttempt.objects.create(student=request.user, quiz=quiz, score=score)
        for question_id, choice in selected_choices.items():
            if choice:
                QuizAnswer.objects.create(attempt=attempt, question_id=question_id, selected_choice=choice)

        # Grading is this view's job; unlocking the next notion is progress's job.
        register_attempt(attempt)

        return render(request, "quizzes/quiz_result.html", {"attempt": attempt, "quiz": quiz})

    return render(request, "quizzes/quiz_take.html", {"quiz": quiz})
