"""Vues du dashboard d'administration eCampus BBT."""
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from courses.models import Notion
from payments.models import Enrollment, Payment, PaymentPlan
from progress.models import StudentProgress
from quizzes.models import QuizAttempt

from .decorators import admin_required
from .forms import AdminStudentForm


@admin_required
def index(request):
    """Vue d'ensemble (dashboard principal)."""
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Statistiques globales
    total_students = CustomUser.objects.filter(role="student").count()
    active_students = CustomUser.objects.filter(role="student", is_active=True).count()

    # Revenus du mois
    monthly_revenue = (
        Payment.objects
        .filter(status="success", verified_at__gte=month_start)
        .aggregate(total=Sum("amount"))["total"] or 0
    )

    # Paiements ce mois
    monthly_payments = (
        Payment.objects
        .filter(status="success", verified_at__gte=month_start)
        .count()
    )

    # Notions complétées récemment (7 derniers jours)
    week_ago = now - timedelta(days=7)
    recent_completions = (
        StudentProgress.objects
        .filter(status="completed", completed_at__gte=week_ago)
        .count()
    )

    # Derniers paiements
    recent_payments = (
        Payment.objects
        .filter(status="success")
        .select_related("enrollment__student", "enrollment__plan")
        .order_by("-verified_at")[:5]
    )

    # Derniers étudiants inscrits
    recent_students = (
        CustomUser.objects
        .filter(role="student")
        .order_by("-date_joined")[:5]
    )

    # Répartition par niveau
    students_by_level = (
        CustomUser.objects
        .filter(role="student", level__isnull=False)
        .values("level")
        .annotate(count=Count("id"))
        .order_by("level")
    )

    context = {
        "total_students": total_students,
        "active_students": active_students,
        "monthly_revenue": monthly_revenue,
        "monthly_payments": monthly_payments,
        "recent_completions": recent_completions,
        "recent_payments": recent_payments,
        "recent_students": recent_students,
        "students_by_level": students_by_level,
        "month_name": month_start.strftime("%B %Y"),
    }
    return render(request, "dashboard_admin/index.html", context)


@admin_required
def students_list(request):
    """Liste de tous les étudiants avec filtres."""
    filter_type = request.GET.get("type", "all")  # all, paid, admin
    search = request.GET.get("q", "").strip()

    qs = CustomUser.objects.filter(role="student").order_by("-date_joined")

    if filter_type == "paid":
        qs = qs.filter(enrollment__isnull=False)
    elif filter_type == "admin":
        qs = qs.filter(enrollment__isnull=True)

    if search:
        qs = qs.filter(
            __import__("django.db.models", fromlist=["Q"]).Q(email__icontains=search)
            | __import__("django.db.models", fromlist=["Q"]).Q(first_name__icontains=search)
            | __import__("django.db.models", fromlist=["Q"]).Q(last_name__icontains=search)
        )

    qs = qs.select_related("enrollment__plan")

    context = {
        "students": qs[:200],  # limite pour performance
        "filter_type": filter_type,
        "search": search,
        "total_count": qs.count(),
    }
    return render(request, "dashboard_admin/students.html", context)


@admin_required
def student_create(request):
    """Création d'un étudiant en présentiel (accès direct, sans paiement)."""
    if request.method == "POST":
        form = AdminStudentForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(
                request,
                f"✅ Étudiant {user.first_name} {user.last_name} créé avec succès. "
                f"Identifiants : {user.email} / (mot de passe transmis)"
            )
            return redirect("dashboard_admin:student_detail", pk=user.pk)
    else:
        form = AdminStudentForm()

    return render(request, "dashboard_admin/student_create.html", {"form": form})


@admin_required
def student_detail(request, pk):
    """Fiche détaillée d'un étudiant : progression, paiements, quiz."""
    student = get_object_or_404(CustomUser, pk=pk, role="student")
    enrollment = getattr(student, "enrollment", None)

    # Progression
    notions = Notion.objects.filter(level=student.level).order_by("order") if student.level else []
    progress_map = {
        p.notion_id: p
        for p in StudentProgress.objects.filter(student=student)
    }

    rows = []
    for notion in notions:
        p = progress_map.get(notion.id)
        rows.append({
            "notion": notion,
            "status": p.status if p else "locked",
            "best_score": p.best_score if p else None,
        })

    # Paiements
    payments = (
        Payment.objects
        .filter(enrollment__student=student)
        .order_by("-created_at")
    ) if enrollment else []

    # Tentatives de quiz
    quiz_attempts = (
        QuizAttempt.objects
        .filter(student=student)
        .select_related("quiz__notion")
        .order_by("-attempted_at")[:20]
    )

    context = {
        "student": student,
        "enrollment": enrollment,
        "rows": rows,
        "payments": payments,
        "quiz_attempts": quiz_attempts,
    }
    return render(request, "dashboard_admin/student_detail.html", context)


@admin_required
def payments_list(request):
    """Historique de tous les paiements, avec filtres."""
    status_filter = request.GET.get("status", "all")
    provider_filter = request.GET.get("provider", "all")

    qs = (
        Payment.objects
        .select_related("enrollment__student", "enrollment__plan")
        .order_by("-created_at")
    )

    if status_filter != "all":
        qs = qs.filter(status=status_filter)
    if provider_filter != "all":
        qs = qs.filter(provider=provider_filter)

    # Totaux
    total_amount = qs.filter(status="success").aggregate(s=Sum("amount"))["s"] or 0

    context = {
        "payments": qs[:500],
        "total_amount": total_amount,
        "status_filter": status_filter,
        "provider_filter": provider_filter,
    }
    return render(request, "dashboard_admin/payments.html", context)


@admin_required
def quiz_attempts_list(request):
    """Historique des tentatives de quiz (analytics)."""
    qs = (
        QuizAttempt.objects
        .select_related("student", "quiz__notion")
        .order_by("-attempted_at")
    )

    passed_filter = request.GET.get("passed", "all")
    if passed_filter == "yes":
        qs = qs.filter(passed=True)
    elif passed_filter == "no":
        qs = qs.filter(passed=False)

    # Stats
    total = qs.count()
    passed = qs.filter(passed=True).count()
    pass_rate = round((passed / total) * 100, 1) if total else 0

    context = {
        "attempts": qs[:300],
        "passed_filter": passed_filter,
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
    }
    return render(request, "dashboard_admin/quiz_attempts.html", context)
