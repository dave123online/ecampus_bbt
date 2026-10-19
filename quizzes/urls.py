from django.urls import path

from . import views

app_name = "quizzes"

urlpatterns = [
    path("<int:pk>/take/", views.quiz_take, name="quiz_take"),
]
