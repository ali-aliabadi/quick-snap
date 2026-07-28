"""Send a guest their own roll as email attachments."""

import threading

from django.conf import settings
from django.core.mail import EmailMessage

# Static thank-you message sent to every guest with their photos attached.
THANK_YOU_BODY = (
    "Thank you for being part of our celebration.\n\n"
    "Your photos are attached. Thank you for helping make the day "
    "more memorable for us.\n"
)


def _send(guest):
    photos = list(guest.photos.all())
    if not photos:
        return
    msg = EmailMessage(
        subject=f"Your photos from {guest.event.name}",
        body=THANK_YOU_BODY,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[guest.email],
    )
    for i, photo in enumerate(photos, start=1):
        photo.image.open("rb")
        try:
            data = photo.image.read()
        finally:
            photo.image.close()
        msg.attach(f"{i:03d}.jpg", data, "image/jpeg")
    msg.send(fail_silently=False)


def send_roll_email_sync(guest):
    """Send synchronously, managing the email_sent flag. Returns True on success.

    Use from management commands / cron, where daemon threads would be killed
    when the process exits before delivery completes.
    """
    try:
        _send(guest)
        return True
    except Exception:
        # Delivery failed; unflag so a later run can retry.
        try:
            guest.email_sent = False
            guest.save(update_fields=["email_sent"])
        except Exception:
            pass
        return False


def send_roll_email(guest):
    """Fire the email in a background thread so the caller returns instantly.

    Guarded by guest.email_sent (set by the caller) to avoid duplicate sends.
    On failure the worker unflags email_sent for retry and never raises out.
    """

    def worker():
        send_roll_email_sync(guest)

    threading.Thread(target=worker, daemon=True).start()
