from django.contrib import admin

from .models import Quiz, Question, Choice, QuizAttempt, QuizAnswer


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 4


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 1


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("notion", "title")
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    inlines = [ChoiceInline]


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    # Full attempt history visible to admin, per Maya's analytics requirement.
    list_display = ("student", "quiz", "score", "passed", "attempted_at")
    list_filter = ("passed", "quiz")
    readonly_fields = ("student", "quiz", "score", "passed", "attempted_at")
