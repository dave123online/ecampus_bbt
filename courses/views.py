import os
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.views.generic import DetailView

from .models import Notion, PDFResource, AudioResource
from .permissions import NotionAccessMixin, notion_access_allowed


class NotionDetailView(NotionAccessMixin, DetailView):
    model = Notion
    template_name = "courses/notion_detail.html"
    context_object_name = "notion"


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 64 * 1024


def _iter_range(path, start, length):
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _protected_file_response(request, file_field, content_type):
    """
    Serves a protected file:
    - Behind Nginx: X-Accel-Redirect   - Behind Apache: X-Sendfile
    - Otherwise (Render, dev): served by Django, with HTTP Range support
      (required for audio seeking on Safari / iOS) and no-store caching.
    """
    file_path = file_field.name

    if getattr(settings, "USE_X_ACCEL_REDIRECT", False):
        response = HttpResponse(content_type=content_type)
        response["Content-Disposition"] = "inline"
        response["X-Accel-Redirect"] = f"/protected-media/{file_path}"
        return response

    if getattr(settings, "USE_X_SENDFILE", False):
        response = HttpResponse(content_type=content_type)
        response["Content-Disposition"] = "inline"
        response["X-Sendfile"] = file_field.path
        return response

    try:
        abs_path = file_field.path
        size = os.path.getsize(abs_path)
    except (ValueError, OSError):
        raise Http404("Fichier introuvable.")

    range_header = request.headers.get("Range", "")
    match = _RANGE_RE.match(range_header.strip()) if range_header else None

    if match and (match.group(1) or match.group(2)):
        if match.group(1) == "":              # suffixe : "bytes=-500"
            length = min(int(match.group(2)), size)
            start = size - length
            end = size - 1
        else:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
            end = min(end, size - 1)
        if start >= size or start > end:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        length = end - start + 1
        response = StreamingHttpResponse(
            _iter_range(abs_path, start, length), status=206, content_type=content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(length)
    else:
        response = FileResponse(open(abs_path, "rb"), content_type=content_type)
        response["Content-Length"] = str(size)

    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = "inline"
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def serve_pdf(request, pk):
    resource = PDFResource.objects.filter(pk=pk).select_related("notion").first()

    if resource is None:
        raise Http404

    if not notion_access_allowed(request.user, resource.notion):
        raise Http404

    return _protected_file_response(request, resource.file, "application/pdf")


@login_required
def serve_audio(request, pk):
    resource = AudioResource.objects.filter(pk=pk).select_related("notion").first()

    if resource is None:
        raise Http404

    if not notion_access_allowed(request.user, resource.notion):
        raise Http404

    return _protected_file_response(request, resource.file, "audio/mpeg")
