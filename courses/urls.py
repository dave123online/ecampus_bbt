from django.urls import path

from . import views

app_name = "courses"

urlpatterns = [
    path("notion/<int:pk>/", views.NotionDetailView.as_view(), name="notion_detail"),
    path("pdf/<int:pk>/", views.serve_pdf, name="serve_pdf"),
    path("audio/<int:pk>/", views.serve_audio, name="serve_audio"),
]
