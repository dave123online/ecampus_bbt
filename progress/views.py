from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from courses.models import Notion
from .models import StudentProgress


@login_required
def dashboard(request):
    user = request.user
    notions = Notion.objects.filter(level=user.level)
    progress_by_notion = {
        p.notion_id: p for p in StudentProgress.objects.filter(student=user, notion__in=notions)
    }

    rows = []
    for notion in notions:
        progress = progress_by_notion.get(notion.id)
        if progress:
            status = progress.status
        else:
            # No StudentProgress row yet: matches courses.permissions.NotionAccessMixin,
            # which treats the first notion of a level as implicitly unlocked.
            status = "in_progress" if notion.is_first_in_level() else "locked"
        rows.append({
            "notion": notion,
            "status": status,
            "best_score": progress.best_score if progress else None,
        })

    completed_count = sum(1 for r in rows if r["status"] == "completed")
    percent_complete = round((completed_count / len(rows)) * 100) if rows else 0

    # Payment schedule for self-registered (paid) students — shows what is
    # paid vs owed and offers a one-click retry for the next tranche.
    enrollment = getattr(user, "enrollment", None)
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
