from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .forms import CustomUserCreationForm
from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    model = CustomUser
    list_display = ("username", "email", "role", "level", "is_active")
    list_filter = ("role", "level")

    fieldsets = UserAdmin.fieldsets + (
        ("Plateforme", {"fields": ("role", "level")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Plateforme", {"fields": ("role", "level")}),
    )
