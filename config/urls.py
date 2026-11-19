from django.contrib import admin
from django.urls import path, include

from config.views import healthz, home_redirect

urlpatterns = [
    path("", home_redirect, name="home"),
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("manage/", include("dashboard_admin.urls")),
    path("accounts/", include("accounts.urls")),
    path("courses/", include("courses.urls")),
    path("quizzes/", include("quizzes.urls")),
    path("progress/", include("progress.urls")),
    path("payments/", include("payments.urls")),
]
