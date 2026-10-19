from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from payments.services import paid_notions_available_for
from progress.models import StudentProgress


def notion_access_allowed(user, notion):
    """Shared gate used by NotionAccessMixin AND the protected file views.

    Rules:
      1. Must be a student whose assigned level matches the notion's level.
      2. Self-registered (paid) students: only the first `paid_notion_count`
         notions of their level are reachable — beyond that, blocked until
         they pay more. Admin-created students have no Enrollment (=unlimited).
      3. Sequential unlocking still applies inside that window: notion N+1
         opens when notion N is completed (progress.services) — except the
         very first notion, which is open once paid for.
    """
    if not user.is_authenticated or not user.is_student():
        return False
    if user.level != notion.level:
        return False

    available = paid_notions_available_for(user)
    if available is not None and notion.order > available:
        return False

    progress = StudentProgress.objects.filter(student=user, notion=notion).first()
    if progress is None:
        # First notion of the level is implicitly unlocked on first visit
        # (for paid students, only if it falls within their paid window —
        # checked above).
        return notion.is_first_in_level()
    return progress.status != "locked"


class NotionAccessMixin(UserPassesTestMixin):
    """Blocks access to a Notion unless notion_access_allowed() says yes."""

    def test_func(self):
        return notion_access_allowed(self.request.user, self.get_object())

    def handle_no_permission(self):
        raise PermissionDenied(
            "Ce contenu n'est pas accessible : niveau différent, non débloqué, "
            "ou hors de votre quota payé."
        )
