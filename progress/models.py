from django.conf import settings
from django.db import models

from courses.models import Notion


class StudentProgress(models.Model):
    """One row per (student, notion). Tracks unlock/completion state.
    Mutated exclusively through progress.services — never set status
    directly from other apps' views."""

    STATUS_CHOICES = [
        ("locked", "Verrouillé"),
        ("in_progress", "En cours"),
        ("completed", "Terminé"),
    ]

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="progress")
    notion = models.ForeignKey(Notion, on_delete=models.CASCADE, related_name="progress_entries")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="locked")
    best_score = models.PositiveIntegerField(null=True, blank=True)
    unlocked_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("student", "notion")
