from django.contrib.auth.models import AbstractUser
from django.db import models

from courses.models import LEVEL_CHOICES


class CustomUser(AbstractUser):
    """
    Extends the default Django user. Students are always created by an
    admin (see accounts/admin.py and accounts/forms.py) and assigned a
    level at creation time — they never self-register.
    """
    ROLE_CHOICES = [
        ("admin", "Administrateur"),
        ("student", "Étudiant"),
    ]

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="student")
    level = models.CharField(
        max_length=2,
        choices=LEVEL_CHOICES,
        null=True,
        blank=True,
        help_text="Niveau attribué par l'admin. Obligatoire si role=student.",
    )

    def is_student(self):
        return self.role == "student"

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
