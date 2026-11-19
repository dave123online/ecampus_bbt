"""Service des médias protégés : Range (seek audio iOS/Safari), accès, cache."""
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CustomUser
from courses.models import AudioResource, Notion

_TMP = tempfile.mkdtemp()
DATA = bytes(range(256)) * 4  # 1024 octets


@override_settings(MEDIA_ROOT=_TMP, USE_X_ACCEL_REDIRECT=False, USE_X_SENDFILE=False)
class RangeServingTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP, ignore_errors=True)

    def setUp(self):
        self.student = CustomUser.objects.create_user(
            username="s@x.com", email="s@x.com", password="pass12345",
            role="student", level="A1")
        notion = Notion.objects.create(level="A1", title="N1", order=1)
        self.audio = AudioResource.objects.create(
            notion=notion, file=SimpleUploadedFile("a.mp3", DATA), title="a")
        self.url = reverse("courses:serve_audio", args=[self.audio.pk])
        self.client.force_login(self.student)

    def test_full_response_advertises_ranges(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Accept-Ranges"], "bytes")
        self.assertEqual(b"".join(r.streaming_content), DATA)
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_partial_range(self):
        r = self.client.get(self.url, HTTP_RANGE="bytes=10-19")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r["Content-Range"], "bytes 10-19/1024")
        self.assertEqual(b"".join(r.streaming_content), DATA[10:20])

    def test_open_ended_and_suffix_ranges(self):
        r = self.client.get(self.url, HTTP_RANGE="bytes=1000-")
        self.assertEqual(b"".join(r.streaming_content), DATA[1000:])
        r = self.client.get(self.url, HTTP_RANGE="bytes=-24")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(b"".join(r.streaming_content), DATA[-24:])

    def test_unsatisfiable_range(self):
        r = self.client.get(self.url, HTTP_RANGE="bytes=5000-6000")
        self.assertEqual(r.status_code, 416)

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)

    def test_other_level_is_404(self):
        self.student.level = "B1"
        self.student.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
