import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


def photo_upload_path(instance, filename):
    """media/<event-slug>/<guest-token>/<uuid>.<ext> — groups by event then guest.

    The extension follows the upload: phones that can encode WebP send .webp,
    the rest send .jpg (see detectEncodeFormat in camera.html).
    """
    ext = (filename.rsplit(".", 1)[-1] or "jpg").lower()
    return (
        f"{instance.guest.event.slug}/{instance.guest.token}/{uuid.uuid4().hex}.{ext}"
    )


class Event(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, help_text="Used in the QR URL: /e/<slug>/")
    # Blank = open event: guests join with just name + phone, no password step.
    password_hash = models.CharField(
        max_length=256, blank=True, default="", editable=False
    )
    roll_size = models.PositiveIntegerField(
        default=10, help_text="Number of photos each guest may take (N)."
    )
    start_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional. Guests can't snap before this time."
    )
    end_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional. Guests can't snap after this time."
    )
    is_active = models.BooleanField(
        default=True, help_text="Uncheck to close the event to new photos."
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    def __str__(self):
        return f"{self.name} (/e/{self.slug}/)"

    def set_password(self, raw_password):
        self.password_hash = make_password(raw_password) if raw_password else ""

    @property
    def requires_password(self):
        """False for open events — the join form then hides the password field."""
        return bool(self.password_hash)

    def check_password(self, raw_password):
        # An event with no password accepts anyone who has the link.
        if not self.requires_password:
            return True
        return check_password(raw_password, self.password_hash)

    @property
    def has_started(self):
        return self.start_at is None or timezone.now() >= self.start_at

    @property
    def has_ended(self):
        return self.end_at is not None and timezone.now() > self.end_at

    @property
    def is_open(self):
        """Event accepts photos: active, started, not ended."""
        return self.is_active and self.has_started and not self.has_ended


class Guest(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="guests")
    name = models.CharField(max_length=200)
    # Canonical `09xxxxxxxxx` (see snap.phones.normalize_phone). This is the roll
    # identity: one roll per number per event, so a guest who loses their session
    # cookie resumes by re-entering the same number.
    phone = models.CharField(max_length=20, db_index=True)
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "phone"], name="unique_guest_phone_per_event"
            )
        ]

    def __str__(self):
        return f"{self.name} @ {self.event.slug} ({self.taken}/{self.event.roll_size})"

    @property
    def taken(self):
        return self.photos.count()

    @property
    def remaining(self):
        return max(0, self.event.roll_size - self.taken)

    @property
    def roll_full(self):
        return self.remaining == 0


class Photo(models.Model):
    guest = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=photo_upload_path)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Photo {self.pk} — {self.guest.name}"
