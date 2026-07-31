"""Tests for Quick Snap: models, guest flow, window gating, phones, admin, i18n."""

import io
import json
import re
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from . import throttle
from .i18n import get_strings
from .models import Event, Guest, Photo
from .phones import normalize_phone

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


def make_webp(color=(30, 120, 200), size=(12, 12)):
    """Return valid WebP bytes. The camera prefers WebP where the browser can
    encode it (~3.6x smaller than JPEG at the same visual quality)."""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "WEBP", quality=90)
    return buf.getvalue()


def upload_webp(name="snap.webp"):
    return SimpleUploadedFile(name, make_webp(), content_type="image/webp")


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
        g = Guest.objects.create(event=self.ev, name="Ann", phone="09120000001")
        self.assertEqual(g.taken, 0)
        self.assertEqual(g.remaining, 3)
        self.assertFalse(g.roll_full)
        for _ in range(3):
            Photo.objects.create(guest=g, image=upload())
        self.assertEqual(g.taken, 3)
        self.assertEqual(g.remaining, 0)
        self.assertTrue(g.roll_full)

    def test_remaining_never_negative(self):
        g = Guest.objects.create(event=self.ev, name="Ann", phone="09120000001")
        for _ in range(5):  # more than roll_size
            Photo.objects.create(guest=g, image=upload())
        self.assertEqual(g.remaining, 0)

    def test_one_roll_per_phone_per_event(self):
        Guest.objects.create(event=self.ev, name="Ann", phone="09120000001")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Guest.objects.create(
                event=self.ev, name="Someone Else", phone="09120000001"
            )

    def test_same_phone_different_events_allowed(self):
        other = make_event(slug="other", name="Other")
        Guest.objects.create(event=self.ev, name="Ann", phone="09120000001")
        Guest.objects.create(event=other, name="Ann", phone="09120000001")
        self.assertEqual(Guest.objects.filter(phone="09120000001").count(), 2)

    def test_same_name_different_phone_allowed(self):
        Guest.objects.create(event=self.ev, name="Ann", phone="09120000001")
        Guest.objects.create(event=self.ev, name="Ann", phone="09120000002")
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
class JoinViewTests(TestCase):
    def setUp(self):
        self.ev = make_event(password="pw", roll_size=3)
        self.url = reverse("snap:join", args=[self.ev.slug])

    def test_get_renders_form(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.ev.name)

    def test_wrong_password_rejected(self):
        r = self.client.post(
            self.url, {"name": "Ann", "phone": "09123456789", "password": "nope"}
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.ev.guests.count(), 0)

    def test_missing_name_rejected(self):
        r = self.client.post(
            self.url, {"name": "", "phone": "09123456789", "password": "pw"}
        )
        self.assertEqual(r.status_code, 400)

    def test_invalid_phone_rejected(self):
        r = self.client.post(
            self.url, {"name": "Ann", "phone": "invalid", "password": "pw"}
        )
        self.assertEqual(r.status_code, 400)

    def test_valid_join_creates_guest_and_redirects(self):
        r = self.client.post(
            self.url, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
        self.assertRedirects(r, reverse("snap:camera", args=[self.ev.slug]))
        self.assertEqual(self.ev.guests.count(), 1)
        g = self.ev.guests.get()
        self.assertEqual(g.phone, "09123456789")

    def test_returning_guest_resumes_same_roll(self):
        # First visit, takes 2 photos.
        self.client.post(
            self.url, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
        g = self.ev.guests.get()
        Photo.objects.create(guest=g, image=upload())
        Photo.objects.create(guest=g, image=upload())
        # New browser (fresh client), same phone → same guest, roll preserved.
        c2 = Client()
        c2.post(self.url, {"name": "Ann", "phone": "09123456789", "password": "pw"})
        self.assertEqual(self.ev.guests.count(), 1)
        self.assertEqual(self.ev.guests.get().remaining, 1)

    def test_phone_normalization(self):
        # +98 country code normalizes to 09
        self.client.post(
            self.url, {"name": "Ann", "phone": "+989123456789", "password": "pw"}
        )
        g = self.ev.guests.get()
        self.assertEqual(g.phone, "09123456789")

    def test_new_phone_starts_fresh_roll(self):
        self.client.post(
            self.url, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
        Client().post(
            self.url, {"name": "Bob", "phone": "09987654321", "password": "pw"}
        )
        self.assertEqual(self.ev.guests.count(), 2)

    def test_join_blocked_when_ended(self):
        self.ev.end_at = timezone.now() - timedelta(minutes=1)
        self.ev.save()
        r = self.client.post(
            self.url, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
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
        self.client.post(
            self.join, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
        r = self.client.get(self.cam)
        self.assertEqual(r.status_code, 200)

    def test_redirects_to_done_when_roll_full(self):
        self.client.post(
            self.join, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
        g = self.ev.guests.get()
        for _ in range(self.ev.roll_size):
            Photo.objects.create(guest=g, image=upload())
        r = self.client.get(self.cam)
        self.assertRedirects(r, reverse("snap:done", args=[self.ev.slug]))

    def _joined_camera(self):
        self.client.post(
            self.join, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
        return self.client.get(self.cam)

    def test_camera_is_ltr_in_english(self):
        self.assertContains(self._joined_camera(), 'dir="ltr"')

    @override_settings(APP_LANG="fa")
    def test_camera_is_rtl_in_persian(self):
        """The viewfinder is its own standalone template, so it has to opt into
        lang/dir itself — otherwise every html[dir="rtl"] rule silently dies."""
        r = self._joined_camera()
        self.assertContains(r, 'lang="fa"')
        self.assertContains(r, 'dir="rtl"')

    @override_settings(APP_LANG="fa")
    def test_camera_loads_persian_font(self):
        """Space Mono has no Persian glyphs; without Vazirmatn the UI falls back."""
        self.assertContains(self._joined_camera(), "Vazirmatn")

    def test_camera_exposes_join_url_for_expired_session(self):
        """capture() answers 403 not_joined once the session lapses; the page
        needs the join URL to send the guest back to resume their roll."""
        self.assertContains(self._joined_camera(), f'data-join-url="{self.join}"')

    def test_camera_has_zoom_control(self):
        self.assertContains(self._joined_camera(), 'id="zoom-pill"')


@override_settings(APP_LANG="en")
class CaptureViewTests(TestCase):
    def setUp(self):
        self.ev = make_event(password="pw", roll_size=2)
        self.join = reverse("snap:join", args=[self.ev.slug])
        self.cap = reverse("snap:capture", args=[self.ev.slug])

    def _join(self, client, **extra):
        data = {"name": "Ann", "phone": "09123456789", "password": "pw"}
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


class DoneViewTests(TestCase):
    @override_settings(APP_LANG="en")
    def test_done_renders(self):
        ev = make_event(password="pw")
        self.client.post(
            reverse("snap:join", args=[ev.slug]),
            {"name": "Ann", "phone": "09123456789", "password": "pw"},
        )
        r = self.client.get(reverse("snap:done", args=[ev.slug]))
        self.assertEqual(r.status_code, 200)

    @override_settings(APP_LANG="en")
    def test_done_interpolates_count_and_event(self):
        """The {n}/{event} holes are filled in the view — templates can't."""
        ev = make_event(name="Ann's Wedding", password="pw", roll_size=5)
        self.client.post(
            reverse("snap:join", args=[ev.slug]),
            {"name": "Ann", "phone": "09123456789", "password": "pw"},
        )
        g = ev.guests.get()
        for _ in range(3):
            Photo.objects.create(guest=g, image=upload())
        r = self.client.get(reverse("snap:done", args=[ev.slug]))
        self.assertContains(r, "You took 3 photos at Ann&#x27;s Wedding.")
        self.assertNotContains(r, "{n}")
        self.assertNotContains(r, "{event}")

    @override_settings(APP_LANG="fa")
    def test_done_interpolates_in_persian(self):
        ev = make_event(name="Mehmooni", password="pw")
        self.client.post(
            reverse("snap:join", args=[ev.slug]),
            {"name": "Ann", "phone": "09123456789", "password": "pw"},
        )
        Photo.objects.create(guest=ev.guests.get(), image=upload())
        r = self.client.get(reverse("snap:done", args=[ev.slug]))
        self.assertNotContains(r, "{n}")
        self.assertNotContains(r, "{event}")

    @override_settings(APP_LANG="en")
    def test_done_without_session_uses_thanks_copy(self):
        """Someone opening /done/ cold has no guest — don't render a bare count."""
        ev = make_event(name="Gala", password="pw")
        r = self.client.get(reverse("snap:done", args=[ev.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Thanks for shooting at Gala.")
        self.assertNotContains(r, "{event}")

    @override_settings(APP_LANG="en")
    def test_done_photo_slots_capped(self):
        """One lit frame per photo, but a huge roll must not render hundreds."""
        ev = make_event(password="pw", roll_size=40)
        self.client.post(
            reverse("snap:join", args=[ev.slug]),
            {"name": "Ann", "phone": "09123456789", "password": "pw"},
        )
        g = ev.guests.get()
        for _ in range(20):
            Photo.objects.create(guest=g, image=upload())
        r = self.client.get(reverse("snap:done", args=[ev.slug]))
        self.assertEqual(len(r.context["photo_slots"]), 12)


# --------------------------------------------------------------------------- #
# Phone normalization
# --------------------------------------------------------------------------- #
class PhoneNormalizationTests(TestCase):
    def test_canonical_09_passes_through(self):
        self.assertEqual(normalize_phone("09123456789"), "09123456789")

    def test_plus98_normalizes(self):
        self.assertEqual(normalize_phone("+989123456789"), "09123456789")

    def test_0098_normalizes(self):
        self.assertEqual(normalize_phone("00989123456789"), "09123456789")

    def test_bare_9_normalizes(self):
        self.assertEqual(normalize_phone("9123456789"), "09123456789")

    def test_persian_digits_normalize(self):
        self.assertEqual(normalize_phone("۰۹۱۲۳۴۵۶۷۸۹"), "09123456789")

    def test_separators_ignored(self):
        self.assertEqual(normalize_phone("0912-345-6789"), "09123456789")
        self.assertEqual(normalize_phone("0912 345 6789"), "09123456789")

    def test_too_short_rejected(self):
        self.assertIsNone(normalize_phone("0912345"))

    def test_invalid_format_rejected(self):
        self.assertIsNone(normalize_phone("invalid"))


# --------------------------------------------------------------------------- #
# Admin ZIP action
# --------------------------------------------------------------------------- #
class AdminZipTests(TestCase):
    def test_download_all_photos_returns_zip(self):
        import zipfile

        from .admin import download_all_photos

        ev = make_event(roll_size=2)
        g = Guest.objects.create(event=ev, name="Ann", phone="09123456789")
        Photo.objects.create(guest=g, image=upload())
        Photo.objects.create(guest=g, image=upload())
        resp = download_all_photos(None, None, Event.objects.filter(pk=ev.pk))
        self.assertEqual(resp["Content-Type"], "application/zip")
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        self.assertEqual(len(zf.namelist()), 2)

    def test_zip_keeps_each_photo_s_real_extension(self):
        """A .webp labelled .jpg is a file some viewers refuse to open, and the
        host only ever sees these through this ZIP."""
        import zipfile

        from .admin import download_all_photos

        ev = make_event(roll_size=4)
        g = Guest.objects.create(event=ev, name="Ann", phone="09123456789")
        Photo.objects.create(guest=g, image=upload())  # jpeg
        Photo.objects.create(guest=g, image=upload_webp())  # webp
        resp = download_all_photos(None, None, Event.objects.filter(pk=ev.pk))
        names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
        exts = sorted(n.rsplit(".", 1)[-1] for n in names)
        self.assertEqual(exts, ["jpg", "webp"])


class WebPCaptureTests(TestCase):
    """WebP is the default capture format where the browser can encode it.
    These cover the server half — the client half is the toBlob type probe."""

    @override_settings(APP_LANG="en")
    def test_webp_upload_is_accepted_and_stored_as_webp(self):
        ev = make_event(password="pw", roll_size=2)
        self.client.post(
            reverse("snap:join", args=[ev.slug]),
            {"name": "Ann", "phone": "09123456789", "password": "pw"},
        )
        r = self.client.post(
            reverse("snap:capture", args=[ev.slug]), {"image": upload_webp()}
        )
        self.assertEqual(r.status_code, 200)
        photo = ev.guests.get().photos.get()
        self.assertTrue(
            photo.image.name.endswith(".webp"),
            f"stored as {photo.image.name!r}, losing the format",
        )

    def test_upload_path_preserves_extension(self):
        from .models import photo_upload_path

        ev = make_event(slug="party")
        g = Guest.objects.create(event=ev, name="Ann", phone="09120000001")
        p = Photo(guest=g)
        self.assertTrue(photo_upload_path(p, "snap.webp").endswith(".webp"))
        self.assertTrue(photo_upload_path(p, "snap.jpg").endswith(".jpg"))

    def test_stored_webp_is_a_real_decodable_image(self):
        """Guards against a browser silently handing us PNG bytes named .webp —
        the documented canvas.toBlob fallback."""
        ev = make_event(roll_size=1)
        g = Guest.objects.create(event=ev, name="Ann", phone="09123456789")
        photo = Photo.objects.create(guest=g, image=upload_webp())
        photo.image.open("rb")
        try:
            img = Image.open(photo.image)
            self.assertEqual(img.format, "WEBP")
        finally:
            photo.image.close()


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

    def test_persian_avoids_film_jargon(self):
        """Guests are not photographers. "حلقه" (film roll) and "فریم" (frame)
        read as gibberish in a party context — say عکس instead."""
        fa = get_strings("fa")
        for key, text in fa.items():
            for jargon in ("حلقه", "فریم", "کیو‌آر"):
                self.assertNotIn(
                    jargon,
                    text,
                    f"{key!r} uses photography jargon {jargon!r}: {text!r}",
                )

    def test_persian_is_consistently_polite(self):
        """The copy mixed informal (بزن/کن) with formal (کنید), which reads as
        sloppy to a guest. Keep the whole guest-facing voice formal-polite.

        Matching is anchored to a word end, because every informal imperative is
        also a prefix of its polite form (کن → کنید, بگیر → بگیرید).
        """
        import re

        fa = get_strings("fa")
        # Bare informal imperatives: the verb, then a word boundary — so `کنید`,
        # `بگیرید`, `بزنید` don't trip it.
        informal = re.compile(
            r"(?:^|\s)(کن|بزن|بگیر|بساز|ببند|بپرس|بده)(?=$|[\s.,؟!،])"
        )
        for key, text in fa.items():
            hit = informal.search(text)
            self.assertIsNone(
                hit,
                f"{key!r} slips into informal address "
                f"({hit.group(1) if hit else ''!r}): {text!r}",
            )

    def test_interpolation_placeholders_are_matched_across_languages(self):
        """A {n}/{event} hole present in one language but not the other means one
        locale renders a literal placeholder to the guest."""
        import re

        fa, en = get_strings("fa"), get_strings("en")
        for key in fa:
            self.assertEqual(
                set(re.findall(r"\{[a-z]+\}", fa[key])),
                set(re.findall(r"\{[a-z]+\}", en[key])),
                f"{key!r} has mismatched placeholders between fa and en",
            )

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

    def test_default_language_is_persian(self):
        """Guests are Persian-speaking, so with no cookie the site opens in fa."""
        r = self.client.get(reverse("landing"))
        self.assertContains(r, 'dir="rtl"')
        self.assertContains(r, 'lang="fa"')

    def test_ui_lang_cookie_overrides_to_english(self):
        self.client.cookies["ui_lang"] = "en"
        r = self.client.get(reverse("landing"))
        self.assertContains(r, 'dir="ltr"')
        self.assertContains(r, 'lang="en"')

    def test_bogus_cookie_falls_back_to_default(self):
        self.client.cookies["ui_lang"] = "xx"
        r = self.client.get(reverse("landing"))
        self.assertContains(r, 'lang="fa"')

    def test_set_language_sets_cookie_and_redirects(self):
        r = self.client.post(
            reverse("set_language"), {"lang": "fa", "next": "/events/"}
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, "/events/")
        self.assertEqual(r.cookies["ui_lang"].value, "fa")

    def test_set_language_rejects_offsite_next(self):
        r = self.client.post(
            reverse("set_language"), {"lang": "fa", "next": "https://evil.example/x"}
        )
        self.assertEqual(r.url, "/")

    def test_set_language_ignores_unknown_lang(self):
        r = self.client.post(reverse("set_language"), {"lang": "xx", "next": "/"})
        self.assertEqual(r.cookies["ui_lang"].value, "en")

    def test_set_language_get_not_allowed(self):
        r = self.client.get(reverse("set_language"))
        self.assertEqual(r.status_code, 405)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
@override_settings(METRICS_TOKEN="test-token")
class MetricsEndpointTests(TestCase):
    """The endpoint exposes guest counts, event slugs and storage figures, and
    every scrape walks the media tree — so the gate is load-bearing, not
    cosmetic."""

    url = "/metrics"

    def test_requires_a_token(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_rejects_wrong_token(self):
        self.assertEqual(self.client.get(self.url + "?token=nope").status_code, 404)

    def test_accepts_query_token(self):
        self.assertEqual(
            self.client.get(self.url + "?token=test-token").status_code, 200
        )

    def test_accepts_bearer_token(self):
        r = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer test-token")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/plain", r["Content-Type"])

    @override_settings(METRICS_TOKEN="", DEBUG=False)
    def test_fails_closed_when_unconfigured_in_production(self):
        """Forgetting to set the token must not silently publish the data."""
        self.assertEqual(self.client.get(self.url).status_code, 404)

    @override_settings(METRICS_TOKEN="", DEBUG=True)
    def test_open_in_debug(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def _scrape(self):
        return self.client.get(self.url + "?token=test-token").content.decode()

    @staticmethod
    def _value(name, labels):
        """Current value of one counter series, or 0.0 if not yet created.

        Counters are process-global and keep climbing across tests in the same
        run, so assertions here compare *deltas* rather than absolute values —
        an absolute assertion would pass or fail depending on test order.
        """
        from prometheus_client import REGISTRY

        return REGISTRY.get_sample_value(name, labels) or 0.0

    def test_counts_captures_and_errors(self):
        ev = make_event(password="pw", roll_size=2)
        join = reverse("snap:join", args=[ev.slug])
        cap = reverse("snap:capture", args=[ev.slug])

        before_ok = self._value("quicksnap_photos_captured_total", {"event": ev.slug})
        before_err = self._value(
            "quicksnap_capture_errors_total", {"reason": "not_joined"}
        )
        before_join = self._value("quicksnap_joins_total", {"result": "created"})

        self.client.post(
            join, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
        self.client.post(cap, {"image": upload()})
        Client().post(cap, {"image": upload()})  # no session -> not_joined

        self.assertEqual(
            self._value("quicksnap_photos_captured_total", {"event": ev.slug})
            - before_ok,
            1.0,
        )
        self.assertEqual(
            self._value("quicksnap_capture_errors_total", {"reason": "not_joined"})
            - before_err,
            1.0,
        )
        self.assertEqual(
            self._value("quicksnap_joins_total", {"result": "created"}) - before_join,
            1.0,
        )
        # And they reach the scrape output, not just the in-process registry.
        self.assertIn("quicksnap_photos_captured_total", self._scrape())

    def test_exposes_live_state_gauges(self):
        """Gauges are read from the DB at scrape time, so they can't drift."""
        ev = make_event(password="pw", roll_size=4)
        self.client.post(
            reverse("snap:join", args=[ev.slug]),
            {"name": "Ann", "phone": "09123456789", "password": "pw"},
        )
        Photo.objects.create(guest=ev.guests.get(), image=upload())

        body = self._scrape()
        self.assertIn("quicksnap_photos 1.0", body)
        self.assertIn("quicksnap_guests 1.0", body)
        self.assertIn('quicksnap_events{state="open"} 1.0', body)
        # 1 guest x roll_size 4 = 4 capacity, 1 taken
        self.assertIn(
            f'quicksnap_event_roll_fill_ratio{{event="{ev.slug}"}} 0.25', body
        )

    def test_request_metrics_use_view_names_not_paths(self):
        """Raw paths would let a 404 scanner mint unbounded label values."""
        self.client.get("/definitely-not-a-real-path-12345")
        body = self._scrape()
        self.assertIn('view="<unmatched>"', body)
        self.assertNotIn("definitely-not-a-real-path", body)

    def test_scrape_survives_a_broken_collector(self):
        """A monitoring endpoint that 500s during an incident is worse than a
        missing panel, so StateCollector swallows its own failures."""
        with mock.patch(
            "snap.models.Photo.objects.count", side_effect=RuntimeError("boom")
        ):
            r = self.client.get(self.url + "?token=test-token")
        self.assertEqual(r.status_code, 200)
        # The event counters still made it out even though the gauges bailed.
        self.assertIn("quicksnap_http_requests_total", r.content.decode())


@override_settings(METRICS_TOKEN="test-token")
class CaptureOutcomeMetricTests(TestCase):
    """Normal gating must not be counted as failure.

    A healthy event ends with every guest's roll full; if that lands in the error
    metric, the dashboard shows a spike of "errors" exactly when things went
    best, and the success-rate gauge sags for no reason.
    """

    @staticmethod
    def _value(name, labels):
        from prometheus_client import REGISTRY

        return REGISTRY.get_sample_value(name, labels) or 0.0

    def test_roll_full_is_gated_not_an_error(self):
        ev = make_event(password="pw", roll_size=1)
        join = reverse("snap:join", args=[ev.slug])
        cap = reverse("snap:capture", args=[ev.slug])
        before_gated = self._value(
            "quicksnap_capture_gated_total", {"reason": "roll_full"}
        )
        before_err = self._value(
            "quicksnap_capture_errors_total", {"reason": "roll_full"}
        )

        self.client.post(
            join, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
        self.client.post(cap, {"image": upload()})  # fills the roll
        r = self.client.post(cap, {"image": upload()})  # one past the cap
        self.assertEqual(r.status_code, 409)

        self.assertEqual(
            self._value("quicksnap_capture_gated_total", {"reason": "roll_full"})
            - before_gated,
            1.0,
        )
        # And it must NOT have gone anywhere near the error counter.
        self.assertEqual(
            self._value("quicksnap_capture_errors_total", {"reason": "roll_full"}),
            before_err,
        )

    def test_time_window_gating_is_not_an_error(self):
        ev = make_event(password="pw")
        join = reverse("snap:join", args=[ev.slug])
        cap = reverse("snap:capture", args=[ev.slug])
        self.client.post(
            join, {"name": "Ann", "phone": "09123456789", "password": "pw"}
        )
        before = self._value("quicksnap_capture_gated_total", {"reason": "ended"})

        ev.end_at = timezone.now() - timedelta(minutes=1)
        ev.save()
        self.assertEqual(self.client.post(cap, {"image": upload()}).status_code, 403)

        self.assertEqual(
            self._value("quicksnap_capture_gated_total", {"reason": "ended"}) - before,
            1.0,
        )

    def test_real_failures_still_count_as_errors(self):
        ev = make_event(password="pw")
        cap = reverse("snap:capture", args=[ev.slug])
        before = self._value("quicksnap_capture_errors_total", {"reason": "not_joined"})
        Client().post(cap, {"image": upload()})
        self.assertEqual(
            self._value("quicksnap_capture_errors_total", {"reason": "not_joined"})
            - before,
            1.0,
        )


class JoinThrottleTests(TestCase):
    """Each password check costs a PBKDF2 hash (~80ms), so an unthrottled join
    form is both a guessing oracle and a cheap way to saturate a 3-core VPS."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # throttle state is global; don't leak between tests
        self.ev = make_event(password="pw")
        self.url = reverse("snap:join", args=[self.ev.slug])

    def _fail(self, client=None):
        c = client or self.client
        return c.post(
            self.url, {"name": "Ann", "phone": "09123456789", "password": "wrong"}
        )

    def test_failures_eventually_return_429(self):
        for _ in range(throttle.MAX_FAILURES):
            self._fail()
        self.assertEqual(self._fail().status_code, 429)

    def test_limit_is_loose_enough_for_a_confused_table(self):
        """All guests at a venue share one NAT/Cloudflare IP, so the limit has to
        tolerate several people fumbling before it bites."""
        for _ in range(throttle.MAX_FAILURES - 1):
            self.assertEqual(self._fail().status_code, 400)

    def test_a_successful_join_clears_the_count(self):
        """One guest mistyping must not leave their whole table near the limit."""
        for _ in range(throttle.MAX_FAILURES - 1):
            self._fail()
        r = self.client.post(
            self.url, {"name": "Bob", "phone": "09120000002", "password": "pw"}
        )
        self.assertEqual(r.status_code, 302)
        # Counter reset, so a later mistake is an ordinary 400 again.
        self.assertEqual(self._fail(Client()).status_code, 400)

    def test_throttle_is_per_event(self):
        """Hammering one event must not lock guests out of a different one."""
        other = make_event(slug="other", name="Other", password="pw2")
        for _ in range(throttle.MAX_FAILURES + 1):
            self._fail()
        r = self.client.post(
            reverse("snap:join", args=[other.slug]),
            {"name": "Ann", "phone": "09123456789", "password": "pw2"},
        )
        self.assertEqual(r.status_code, 302)

    def test_throttle_is_per_ip(self):
        blocked = Client(REMOTE_ADDR="10.0.0.1")
        for _ in range(throttle.MAX_FAILURES + 1):
            blocked.post(
                self.url,
                {"name": "Ann", "phone": "09123456789", "password": "wrong"},
            )
        # A different guest on a different IP is unaffected.
        other = Client(REMOTE_ADDR="10.0.0.2")
        r = other.post(
            self.url, {"name": "Bob", "phone": "09120000002", "password": "pw"}
        )
        self.assertEqual(r.status_code, 302)

    def test_blocked_client_never_pays_for_a_hash(self):
        """The whole point is skipping the expensive check, so assert we never
        reach it while blocked."""
        for _ in range(throttle.MAX_FAILURES):
            self._fail()
        with mock.patch.object(
            Event, "check_password", side_effect=AssertionError("should not hash")
        ):
            self.assertEqual(self._fail().status_code, 429)

    def test_cf_connecting_ip_is_preferred(self):
        """Behind Cloudflare + nginx, REMOTE_ADDR is nginx and X-Real-IP is
        Cloudflare's edge — only CF-Connecting-IP identifies the visitor."""
        req = mock.Mock()
        req.META = {
            "HTTP_CF_CONNECTING_IP": "203.0.113.9",
            "HTTP_X_FORWARDED_FOR": "198.51.100.1, 203.0.113.9",
            "REMOTE_ADDR": "172.18.0.1",
        }
        self.assertEqual(throttle.client_ip(req), "203.0.113.9")

    def test_falls_back_through_xff_then_remote_addr(self):
        req = mock.Mock()
        req.META = {
            "HTTP_X_FORWARDED_FOR": "198.51.100.7, 10.0.0.1",
            "REMOTE_ADDR": "172.18.0.1",
        }
        self.assertEqual(throttle.client_ip(req), "198.51.100.7")
        req.META = {"REMOTE_ADDR": "172.18.0.1"}
        self.assertEqual(throttle.client_ip(req), "172.18.0.1")

    def test_throttled_joins_are_counted(self):
        from prometheus_client import REGISTRY

        before = (
            REGISTRY.get_sample_value("quicksnap_joins_total", {"result": "throttled"})
            or 0.0
        )
        for _ in range(throttle.MAX_FAILURES + 1):
            self._fail()
        after = (
            REGISTRY.get_sample_value("quicksnap_joins_total", {"result": "throttled"})
            or 0.0
        )
        self.assertGreaterEqual(after - before, 1.0)


class DashboardTests(TestCase):
    """The Grafana dashboard is committed JSON, so it can be checked like code.
    A typo'd metric name renders an empty panel forever and nobody notices."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = (
            Path(settings.BASE_DIR)
            / "deploy/monitoring/grafana/dashboards/quicksnap.json"
        )
        cls.dash = json.loads(path.read_text())

    def test_panel_ids_are_unique(self):
        ids = [p["id"] for p in self.dash["panels"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_panels_stay_inside_the_grid(self):
        for p in self.dash["panels"]:
            g = p["gridPos"]
            self.assertLessEqual(
                g["x"] + g["w"], 24, f"{p.get('title')} overflows the 24-col grid"
            )

    def test_panels_do_not_overlap(self):
        seen = {}
        for p in self.dash["panels"]:
            g = p["gridPos"]
            for y in range(g["y"], g["y"] + g["h"]):
                for x in range(g["x"], g["x"] + g["w"]):
                    prev = seen.get((x, y))
                    self.assertIsNone(
                        prev,
                        f"{p.get('title')!r} overlaps {prev!r} at ({x},{y})",
                    )
                    seen[(x, y)] = p.get("title")

    def test_every_quicksnap_metric_queried_is_actually_emitted(self):
        from prometheus_client import generate_latest

        from .metrics import registry

        emitted = set()
        for line in generate_latest(registry()).decode().splitlines():
            if line.startswith("# TYPE"):
                emitted.add(line.split()[2])
        # The DB-size gauge is skipped on an in-memory test database by design.
        emitted.add("quicksnap_database_bytes")

        def family(name):
            for suffix in ("_bucket", "_total", "_sum", "_count"):
                if name.endswith(suffix):
                    return name[: -len(suffix)]
            return name

        queried = set()
        for panel in self.dash["panels"]:
            for t in panel.get("targets", []):
                queried |= set(re.findall(r"\b(quicksnap_[a-z_]+)", t["expr"]))

        missing = sorted(
            q for q in queried if family(q) not in emitted and q not in emitted
        )
        self.assertEqual(missing, [], f"dashboard queries unemitted metrics: {missing}")

    def test_no_dual_axis_panels(self):
        """Two y-scales on one chart is the single worst chart mistake."""
        for p in self.dash["panels"]:
            for ov in p.get("fieldConfig", {}).get("overrides", []):
                props = [x["id"] for x in ov.get("properties", [])]
                self.assertNotIn("custom.axisPlacement", props)


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
        self.assertEqual(
            [i["event"].pk for i in r.context["items"] if i["status"] == "soon"],
            [soon.pk],
        )

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
