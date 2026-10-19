from django import forms

from courses.models import LEVEL_CHOICES
from payments.models import PaymentPlan

from .models import CustomUser


class CustomUserCreationForm(forms.ModelForm):
    """
    Used exclusively from the Django admin by the platform administrator
    to create a student account and assign its level in one step.
    (Password handling lives in ModelAdmin.add_view — see accounts/admin.py.)
    """

    class Meta:
        model = CustomUser
        fields = ("username", "email", "role", "level")

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        level = cleaned_data.get("level")
        if role == "student" and not level:
            self.add_error("level", "Un niveau est requis pour un compte étudiant.")
        return cleaned_data


class SelfRegistrationForm(forms.Form):
    """Public signup for walk-in students — access granted by payment only.

    Creates a pending (inactive) CustomUser + an Enrollment; the account is
    activated by the first verified payment (payments.services). No manual
    validation step exists by design.
    """
    full_name = forms.CharField(label="Nom complet", max_length=150)
    email = forms.EmailField(label="E-mail")
    phone = forms.CharField(
        label="Téléphone (Mobile Money)", max_length=20,
        help_text="Numéro utilisé pour le paiement KkiaPay, ex. +229…",
    )
    level = forms.ChoiceField(label="Votre niveau d'anglais", choices=LEVEL_CHOICES)
    plan = forms.ModelChoiceField(
        label="Formule d'accès",
        queryset=PaymentPlan.objects.filter(active=True),
        widget=forms.RadioSelect,
    )
    password = forms.CharField(label="Mot de passe", widget=forms.PasswordInput, min_length=8)
    password2 = forms.CharField(label="Confirmation du mot de passe",
                                widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cet e-mail.")
        return email

    def clean_phone(self):
        phone = "".join(c for c in self.cleaned_data["phone"] if c.isdigit() or c == "+")
        if len(phone.replace("+", "")) < 8:
            raise forms.ValidationError("Numéro de téléphone invalide.")
        return phone

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Les mots de passe ne correspondent pas.")
        return cleaned

    def create_user_and_enrollment(self):
        cd = self.cleaned_data
        user = CustomUser.objects.create_user(
            username=cd["email"],  # e-mail doubles as login
            email=cd["email"], password=cd["password"],
            first_name=cd["full_name"], role="student", level=cd["level"],
            is_active=False,  # activated on first verified payment
        )
        CustomUser.objects.filter(pk=user.pk).update(phone_number=cd["phone"])
        enrollment = cd["plan"].enrollments.create(student=user)
        return user, enrollment
