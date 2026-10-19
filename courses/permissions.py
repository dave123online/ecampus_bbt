from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from progress.models import StudentProgress


class NotionAccessMixin(UserPassesTestMixin):
    """
    Blocks access to a Notion unless:
      1. The student's assigned level matches the notion's level, AND
      2. StudentProgress for this notion is not 'locked'.
    Used by courses.views.NotionDetailView.
    """

    def test_func(self):
        user = self.request.user
        notion = self.get_object()

        if not user.is_student() or user.level != notion.level:
            return False

        progress = StudentProgress.objects.filter(student=user, notion=notion).first()
        if progress is None:
            # First notion of the level is implicitly unlocked on first visit.
            return notion.is_first_in_level()
        return progress.status != "locked"

    def handle_no_permission(self):
        raise PermissionDenied("Ce contenu n'est pas accessible à votre niveau ou n'est pas encore débloqué.")
