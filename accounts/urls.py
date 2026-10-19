from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import PlatformLoginView, register

app_name = "accounts"

urlpatterns = [
    path("login/", PlatformLoginView.as_view(), name="login"),
    path("register/", register, name="register"),
    path("logout/", LogoutView.as_view(next_page="accounts:login"), name="logout"),
]
