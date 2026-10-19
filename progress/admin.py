from django.contrib import admin

from .models import StudentProgress


@admin.register(StudentProgress)
class StudentProgressAdmin(admin.ModelAdmin):
    list_display = ("student", "notion", "status", "best_score", "completed_at")
    list_filter = ("status", "notion__level")
    readonly_fields = ("student", "notion", "status", "best_score", "unlocked_at", "completed_at")
