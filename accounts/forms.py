from django.contrib.auth.forms import UserCreationForm

from .models import CustomUser


class CustomUserCreationForm(UserCreationForm):
    """
    Used exclusively from the Django admin by the platform administrator
    to create a student account and assign its level in one step.
    """

    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ("username", "email", "role", "level")

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        level = cleaned_data.get("level")
        if role == "student" and not level:
            self.add_error("level", "Un niveau est requis pour un compte étudiant.")
        return cleaned_data
