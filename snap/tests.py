"""Tests for Quick Snap: models, guest flow, window gating, email, admin, i18n."""

import io
import shutil
import tempfile
from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .emails import _send
from .i18n import get_strings
from .models import Event, Guest, Photo

_media_override = None


def setUpModule():
    """Route uploaded photos to a throwaway temp dir for the whole module."""
    global _media_override
    _media_override = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    _media_override.enable()


def tearDownModule():
    root = settings.MEDIA_ROOT
    _media_override.disable()
    shutil.rmtree(root, ignore_errors=True)


def make_jpeg(color=(200, 30, 30), size=(12, 12)):
    """Return valid JPEG bytes so ImageField/Pillow validation passes."""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def upload(name="snap.jpg"):
    return SimpleUploadedFile(name, make_jpeg(), content_type="image/jpeg")


def make_event(password="secret", roll_size=3, **kwargs):
    ev = Event(
        name=kwargs.pop("name", "Wedding"),
        slug=kwargs.pop("slug", "wedding"),
        roll_size=roll_size,
        **kwargs,
    )
    ev.set_password(password)
    ev.save()
    return ev


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EventModelTests(TestCase):
    def test_password_is_hashed_and_verified(self):
        ev = make_event(password="hunter2")
        self.assertNotEqual(ev.password_hash, "hunter2")
        self.assertTrue(ev.check_password("hunter2"))
        self.assertFalse(ev.check_password("wrong"))

    def test_open_when_no_window_set(self):
        ev = make_event()
        self.assertTrue(ev.has_started)
        self.assertFalse(ev.has_ended)
        self.assertTrue(ev.is_open)

    def test_not_started_before_start_at(self):
        ev = make_event(start_at=timezone.now() + timedelta(hours=1))
        self.assertFalse(ev.has_started)
        self.assertFalse(ev.is_open)

    def test_ended_after_end_at(self):
        ev = make_event(end_at=timezone.now() - timedelta(minutes=1))
        self.assertTrue(ev.has_ended)
        self.assertFalse(ev.is_open)

    def test_open_within_window(self):
        ev = make_event(
            start_at=timezone.now() - timedelta(hours=1),
            end_at=timezone.now() + timedelta(hours=1),
        )
        self.assertTrue(ev.is_open)

    def test_inactive_is_not_open(self):
        ev = make_event(is_active=False)
        self.assertFalse(ev.is_open)


class GuestModelTests(TestCase):
    def setUp(self):
        self.ev = make_event(roll_size=3)

    def test_taken_remaining_full(self):
        g = Guest.objects.create(event=self.ev, name="Ann")
        self.assertEqual(g.taken, 0)
        self.assertEqual(g.remaining, 3)
        self.assertFalse(g.roll_full)
        for _ in range(3):
            Photo.objects.create(guest=g, image=upload())
        self.assertEqual(g.taken, 3)
        self.assertEqual(g.remaining, 0)
        self.assertTrue(g.roll_full)

    def test_remaining_never_negative(self):
        g = Guest.objects.create(event=self.ev, name="Ann")
        for _ in range(5):  # more than roll_size
            Photo.objects.create(guest=g, image=upload())
        self.assertEqual(g.remaining, 0)

    def test_unique_guest_per_event(self):
        Guest.objects.create(event=self.ev, name="Ann", email="a@x.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Guest.objects.create(event=self.ev, name="Ann", email="a@x.com")

    def test_same_name_different_email_allowed(self):
        Guest.objects.create(event=self.ev, name="Ann", email="a@x.com")
        Guest.objects.create(event=self.ev, name="Ann", email="b@x.com")
        self.assertEqual(self.ev.guests.count(), 2)


class PhotoPathTests(TestCase):
    def test_upload_path_groups_by_event_and_guest(self):
        from .models import photo_upload_path

        ev = make_event(slug="party")
        g = Guest.objects.create(event=ev, name="Ann")
        p = Photo(guest=g)
        path = photo_upload_path(p, "whatever.PNG")
        self.assertTrue(path.startswith(f"party/{g.token}/"))
        self.assertTrue(path.endswith(".png"))


# --------------------------------------------------------------------------- #
# Guest flow (views)
# --------------------------------------------------------------------------- #
@override_settings(APP_LANG="en")
class JoinViewTests(TestCase):
    def setUp(self):
        self.ev = make_event(password="pw", roll_size=3)
        self.url = reverse("snap:join", args=[self.ev.slug])

    def test_get_renders_form(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.ev.name)

    def test_wrong_password_rejected(self):
        r = self.client.post(self.url, {"name": "Ann", "password": "nope"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.ev.guests.count(), 0)

    def test_missing_name_rejected(self):
        r = self.client.post(self.url, {"name": "", "password": "pw"})
        self.assertEqual(r.status_code, 400)

    def test_valid_join_creates_guest_and_redirects(self):
        r = self.client.post(
            self.url, {"name": "Ann", "email": "a@x.com", "password": "pw"}
        )
        self.assertRedirects(r, reverse("snap:camera", args=[self.ev.slug]))
        self.assertEqual(self.ev.guests.count(), 1)

    def test_returning_guest_resumes_same_roll(self):
        # First visit, takes 2 photos.
        self.client.post(
            self.url, {"name": "Ann", "email": "a@x.com", "password": "pw"}
        )
        g = self.ev.guests.get()
        Photo.objects.create(guest=g, image=upload())
        Photo.objects.create(guest=g, image=upload())
        # New browser (fresh client), same name+email → same guest, roll preserved.
        c2 = Client()
        c2.post(self.url, {"name": "Ann", "email": "a@x.com", "password": "pw"})
        self.assertEqual(self.ev.guests.count(), 1)
        self.assertEqual(self.ev.guests.get().remaining, 1)

    def test_new_name_starts_fresh_roll(self):
        self.client.post(
            self.url, {"name": "Ann", "email": "a@x.com", "password": "pw"}
        )
        Client().post(self.url, {"name": "Bob", "email": "b@x.com", "password": "pw"})
        self.assertEqual(self.ev.guests.count(), 2)

    def test_join_blocked_when_ended(self):
        self.ev.end_at = timezone.now() - timedelta(minutes=1)
        self.ev.save()
        r = self.client.post(self.url, {"name": "Ann", "password": "pw"})
        self.assertEqual(r.status_code, 400)


@override_settings(APP_LANG="en")
class CameraViewTests(TestCase):
    def setUp(self):
        self.ev = make_event(password="pw")
        self.cam = reverse("snap:camera", args=[self.ev.slug])
        self.join = reverse("snap:join", args=[self.ev.slug])

    def test_redirects_to_join_when_not_joined(self):
        r = self.client.get(self.cam)
        self.assertRedirects(r, self.join)

    def test_renders_after_join(self):
        self.client.post(self.join, {"name": "Ann", "password": "pw"})
        r = self.client.get(self.cam)
        self.assertEqual(r.status_code, 200)

    def test_redirects_to_done_when_roll_full(self):
        self.client.post(self.join, {"name": "Ann", "password": "pw"})
        g = self.ev.guests.get()
        for _ in range(self.ev.roll_size):
            Photo.objects.create(guest=g, image=upload())
        r = self.client.get(self.cam)
        self.assertRedirects(r, reverse("snap:done", args=[self.ev.slug]))


@override_settings(
    APP_LANG="en", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
)
class CaptureViewTests(TestCase):
    def setUp(self):
        self.ev = make_event(password="pw", roll_size=2)
        self.join = reverse("snap:join", args=[self.ev.slug])
        self.cap = reverse("snap:capture", args=[self.ev.slug])

    def _join(self, client, **extra):
        data = {"name": "Ann", "password": "pw"}
        data.update(extra)
        client.post(self.join, data)

    def test_capture_requires_join(self):
        r = self.client.post(self.cap, {"image": upload()})
        self.assertEqual(r.status_code, 403)

    def test_capture_saves_and_decrements(self):
        self._join(self.client)
        r = self.client.post(self.cap, {"image": upload()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["remaining"], 1)
        self.assertFalse(r.json()["done"])
        self.assertEqual(self.ev.guests.get().taken, 1)

    def test_capture_missing_image_400(self):
        self._join(self.client)
        r = self.client.post(self.cap, {})
        self.assertEqual(r.status_code, 400)

    def test_server_side_roll_cap(self):
        self._join(self.client)
        self.client.post(self.cap, {"image": upload()})
        r = self.client.post(self.cap, {"image": upload()})
        self.assertTrue(r.json()["done"])
        # One past the cap → rejected, count stays at roll_size.
        r = self.client.post(self.cap, {"image": upload()})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.ev.guests.get().taken, 2)

    def test_capture_blocked_before_start(self):
        self.ev.start_at = timezone.now() + timedelta(hours=1)
        self.ev.save()
        self._join(self.client)
        r = self.client.post(self.cap, {"image": upload()})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "not_started")

    def test_capture_blocked_after_end(self):
        self._join(self.client)  # join while open
        self.ev.end_at = timezone.now() - timedelta(minutes=1)
        self.ev.save()
        r = self.client.post(self.cap, {"image": upload()})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "ended")

    def test_email_flag_set_on_roll_full(self):
        self._join(self.client, email="a@x.com")
        self.client.post(self.cap, {"image": upload()})
        self.client.post(self.cap, {"image": upload()})  # fills roll
        self.assertTrue(self.ev.guests.get().email_sent)

    def test_no_email_flag_without_email(self):
        self._join(self.client)  # no email
        self.client.post(self.cap, {"image": upload()})
        self.client.post(self.cap, {"image": upload()})
        self.assertFalse(self.ev.guests.get().email_sent)


class DoneViewTests(TestCase):
    @override_settings(APP_LANG="en")
    def test_done_renders(self):
        ev = make_event(password="pw")
        self.client.post(
            reverse("snap:join", args=[ev.slug]), {"name": "Ann", "password": "pw"}
        )
        r = self.client.get(reverse("snap:done", args=[ev.slug]))
        self.assertEqual(r.status_code, 200)


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailTests(TestCase):
    def test_send_attaches_all_photos(self):
        ev = make_event(roll_size=3)
        g = Guest.objects.create(event=ev, name="Ann", email="a@x.com")
        for _ in range(3):
            Photo.objects.create(guest=g, image=upload())
        _send(g)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["a@x.com"])
        self.assertEqual(len(msg.attachments), 3)

    def test_send_noop_without_photos(self):
        ev = make_event()
        g = Guest.objects.create(event=ev, name="Ann", email="a@x.com")
        _send(g)
        self.assertEqual(len(mail.outbox), 0)


# --------------------------------------------------------------------------- #
# Admin ZIP action
# --------------------------------------------------------------------------- #
class AdminZipTests(TestCase):
    def test_download_all_photos_returns_zip(self):
        import zipfile

        from .admin import download_all_photos

        ev = make_event(roll_size=2)
        g = Guest.objects.create(event=ev, name="Ann")
        Photo.objects.create(guest=g, image=upload())
        Photo.objects.create(guest=g, image=upload())
        resp = download_all_photos(None, None, Event.objects.filter(pk=ev.pk))
        self.assertEqual(resp["Content-Type"], "application/zip")
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        self.assertEqual(len(zf.namelist()), 2)


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #
class I18nTests(TestCase):
    def test_persian_is_rtl(self):
        s = get_strings("fa")
        self.assertEqual(s["dir"], "rtl")
        self.assertEqual(s["lang"], "fa")

    def test_english_is_ltr(self):
        s = get_strings("en")
        self.assertEqual(s["dir"], "ltr")

    def test_unknown_lang_falls_back_to_english(self):
        self.assertEqual(get_strings("xx"), get_strings("en"))

    def test_all_keys_present_in_both(self):
        self.assertEqual(set(get_strings("fa")), set(get_strings("en")))

    @override_settings(APP_LANG="fa")
    def test_join_page_is_rtl_in_persian(self):
        ev = make_event(password="pw")
        r = self.client.get(reverse("snap:join", args=[ev.slug]))
        self.assertContains(r, 'dir="rtl"')

    @override_settings(APP_LANG="en")
    def test_join_page_is_ltr_in_english(self):
        ev = make_event(password="pw")
        r = self.client.get(reverse("snap:join", args=[ev.slug]))
        self.assertContains(r, 'dir="ltr"')


class PublicPagesTests(TestCase):
    def test_landing_renders(self):
        r = self.client.get(reverse("landing"))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "snap/landing.html")

    def test_events_lists_open_and_upcoming(self):
        make_event(name="Open now", slug="open-now", password="pw")
        soon = make_event(
            name="Starts later",
            slug="starts-later",
            password="pw",
            start_at=timezone.now() + timedelta(days=1),
        )
        r = self.client.get(reverse("events"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Open now")
        self.assertContains(r, "Starts later")
        # upcoming event is flagged, not shown as open
        self.assertEqual([i["event"].pk for i in r.context["items"] if i["status"] == "soon"], [soon.pk])

    def test_events_hides_inactive_and_ended(self):
        make_event(name="Closed", slug="closed", password="pw", is_active=False)
        make_event(
            name="Finished",
            slug="finished",
            password="pw",
            end_at=timezone.now() - timedelta(hours=1),
        )
        r = self.client.get(reverse("events"))
        self.assertNotContains(r, "Closed")
        self.assertNotContains(r, "Finished")

    def test_events_empty_state(self):
        r = self.client.get(reverse("events"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.context["items"]), [])
