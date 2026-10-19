from django.contrib import admin
from django.urls import path, include

from config.views import home_redirect

urlpatterns = [
    path("", home_redirect, name="home"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("courses/", include("courses.urls")),
    path("quizzes/", include("quizzes.urls")),
    path("progress/", include("progress.urls")),
]
