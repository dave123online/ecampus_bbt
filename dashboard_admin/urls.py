from django.urls import path
from . import views

app_name = "dashboard_admin"

urlpatterns = [
    path("", views.index, name="index"),
    path("students/", views.students_list, name="students"),
    path("students/new/", views.student_create, name="student_create"),
    path("students/<int:pk>/", views.student_detail, name="student_detail"),
    path("payments/", views.payments_list, name="payments"),
    path("quiz/", views.quiz_attempts_list, name="quiz_attempts"),
]
