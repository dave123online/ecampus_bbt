from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from payments.services import paid_notions_available_for
from progress.models import StudentProgress


def notion_access_allowed(user, notion):
    """
    Shared gate used by NotionAccessMixin AND protected file views.

    Rules:
    1. Must be a student whose assigned level matches the notion's level.
    2. Admin-created students (no Enrollment): unlimited access, no sequential constraint.
    3. Paid students: only the first paid_notion_count notions are reachable.
    4. Sequential unlocking is enforced for paid students:
       notion N+1 opens only when notion N is completed.
    """

    if not user.is_authenticated or not user.is_student():
        return False

    if user.level != notion.level:
        return False

    available = paid_notions_available_for(user)

    # Admin-created students (no Enrollment) have unlimited access
    # without sequential constraints
    if available is None:
        return True

    # Paid students: check paid window
    if notion.order > available:
        return False

    progress = StudentProgress.objects.filter(student=user, notion=notion).first()

    # First notion of the level is accessible if inside the paid window.
    if notion.is_first_in_level():
        return progress is None or progress.status != "locked"

    # For all following notions, the previous one must be completed.
    previous_progress = StudentProgress.objects.filter(
        student=user,
        notion__level=notion.level,
        notion__order=notion.order - 1,
    ).first()

    if previous_progress is None or previous_progress.status != "completed":
        return False

    # If no progress row exists yet, keep it locked by default.
    if progress is None:
        return False

    return progress.status != "locked"


class NotionAccessMixin(UserPassesTestMixin):
    """
    Blocks access to a Notion unless notion_access_allowed() says yes.
    """

    def test_func(self):
        return notion_access_allowed(self.request.user, self.get_object())

    def handle_no_permission(self):
        raise PermissionDenied(
            "Ce contenu n'est pas accessible : niveau différent, non débloqué, "
            "ou hors de votre quota payé."
        )
