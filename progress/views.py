from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from courses.models import Notion

from .models import StudentProgress


@login_required
def dashboard(request):
    user = request.user
    notions = Notion.objects.filter(level=user.level).order_by("order")

    progress_by_notion = {
        p.notion_id: p
        for p in StudentProgress.objects.filter(student=user, notion__in=notions)
    }

    enrollment = getattr(user, "enrollment", None)
    paid_available = enrollment.paid_notion_count() if enrollment is not None else None

    rows = []
    previous_status = None

    for notion in notions:
        progress = progress_by_notion.get(notion.id)

        # 1) Paid window check
        if paid_available is not None and notion.order > paid_available:
            status = "locked"
        elif progress:
            status = progress.status
        else:
            status = "in_progress" if notion.is_first_in_level() else "locked"

        # 2) Sequential check for dashboard consistency
        if not notion.is_first_in_level():
            if previous_status != "completed":
                status = "locked"

        rows.append({
            "notion": notion,
            "status": status,
            "best_score": progress.best_score if progress else None,
        })

        previous_status = status

    completed_count = sum(1 for r in rows if r["status"] == "completed")
    percent_complete = round((completed_count / len(rows)) * 100) if rows else 0

    payment_ctx = None
    if enrollment is not None:
        from payments.providers import next_tranche_state

        tranche_index, amount = next_tranche_state(enrollment)

        payment_ctx = {
            "enrollment": enrollment,
            "tranche_index": tranche_index,
            "amount": amount,
            "tranches_total": enrollment.plan.max_installments,
            "paid_notions": enrollment.paid_notion_count(),
        }

    return render(request, "progress/dashboard.html", {
        "rows": rows,
        "percent_complete": percent_complete,
        "payment": payment_ctx,
    })
