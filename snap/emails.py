"""Send a guest their own roll as email attachments when it fills up."""

import threading

from django.conf import settings
from django.core.mail import EmailMessage


def _send(guest):
    photos = list(guest.photos.all())
    if not photos:
        return
    msg = EmailMessage(
        subject=f"Your photos from {guest.event.name}",
        body=(
            f"Hi {guest.name},\n\n"
            f"Here are the {len(photos)} photos you took at {guest.event.name}. "
            f"Thanks for celebrating with us!\n"
        ),
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


def send_roll_email(guest):
    """Fire the email in a background thread so the final capture returns instantly.

    Guarded by guest.email_sent (set by the caller) to avoid duplicate sends.
    """

    def worker():
        try:
            _send(guest)
        except Exception:
            # Delivery failed; unflag so a later attempt can retry. Guard the
            # write too — this runs in a daemon thread and must never raise out.
            try:
                guest.email_sent = False
                guest.save(update_fields=["email_sent"])
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()
