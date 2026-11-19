"""Formulaires pour le dashboard admin (création d'étudiants en présentiel, etc.)."""
from django import forms
from accounts.models import CustomUser
from courses.models import LEVEL_CHOICES


class AdminStudentForm(forms.ModelForm):
    """Création d'un étudiant en présentiel (sans paiement, accès direct)."""

    password = forms.CharField(
        label="Mot de passe provisoire",
        widget=forms.PasswordInput,
        min_length=8,
        help_text="À communiquer à l'étudiant. Il pourra le changer ensuite.",
    )

    class Meta:
        model = CustomUser
        fields = ("first_name", "last_name", "email", "phone_number", "level")
        widgets = {
            "level": forms.Select(choices=[("", "--- Choisir un niveau ---")] + LEVEL_CHOICES),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["level"].required = True

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cet e-mail.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"].lower()
        user.set_password(self.cleaned_data["password"])
        user.role = "student"
        user.is_active = True  # accès immédiat, pas de paiement requis
        if commit:
            user.save()
        return user
