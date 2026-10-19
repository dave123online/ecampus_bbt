from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.views.generic import DetailView

from .models import Notion, PDFResource, AudioResource
from .permissions import NotionAccessMixin, notion_access_allowed


class NotionDetailView(NotionAccessMixin, DetailView):
    model = Notion
    template_name = "courses/notion_detail.html"
    context_object_name = "notion"


@login_required
def serve_pdf(request, pk):
    """
    Streams the PDF through Django rather than exposing a public media URL.
    The template loads this endpoint into PDF.js (canvas rendering) —
    see static/js/pdf-viewer.js. No Content-Disposition: attachment,
    so no browser 'Save As' is triggered by default.
    """
    resource = PDFResource.objects.filter(pk=pk).select_related("notion").first()
    if resource is None:
        raise Http404
    if not notion_access_allowed(request.user, resource.notion):
        raise Http404
    return FileResponse(resource.file.open("rb"), content_type="application/pdf")


@login_required
def serve_audio(request, pk):
    resource = AudioResource.objects.filter(pk=pk).select_related("notion").first()
    if resource is None:
        raise Http404
    if not notion_access_allowed(request.user, resource.notion):
        raise Http404
    return FileResponse(resource.file.open("rb"), content_type="audio/mpeg")
