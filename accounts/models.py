from django.contrib.auth.models import AbstractUser
from django.db import models

from courses.models import LEVEL_CHOICES


class CustomUser(AbstractUser):
    """
    Extends the default Django user. Two ways in:
    - Classroom students: created by the manager (admin), given credentials.
    - Walk-in students: self-register via accounts.SelfRegistrationForm and
      pay for access (payments app); their account stays inactive until the
      first verified payment.
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
    phone_number = models.CharField(
        max_length=20, blank=True,
        help_text="Numéro Mobile Money (auto-inscrits, pour KkiaPay).",
    )

    def is_student(self):
        return self.role == "student"

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
