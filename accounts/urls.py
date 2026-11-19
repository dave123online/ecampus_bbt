from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import (
    PlatformLoginView,
    recover_checkout,
    register,
    resend_recovery_email,
)

app_name = "accounts"

urlpatterns = [
    path("login/", PlatformLoginView.as_view(), name="login"),
    path("register/", register, name="register"),
    path("logout/", LogoutView.as_view(next_page="accounts:login"), name="logout"),

    # Recovery / checkout resume
    path("recover/<str:token>/", recover_checkout, name="recover_checkout"),
    path("resend-recovery/", resend_recovery_email, name="resend_recovery"),
]
