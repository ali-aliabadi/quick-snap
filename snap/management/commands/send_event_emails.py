"""Send roll emails to all guests of ended events who haven't been emailed yet."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from snap.emails import send_roll_email_sync
from snap.models import Event, Guest


class Command(BaseCommand):
    help = "Send photos to guests of ended events (run via cron after event ends)."

    def handle(self, *args, **options):
        # An event has "ended" when its end_at has passed (matches
        # Event.has_ended). Events with no end_at never auto-send.
        ended_events = Event.objects.filter(
            end_at__isnull=False,
            end_at__lt=timezone.now(),
        )
        sent = 0
        for event in ended_events:
            guests = Guest.objects.filter(
                event=event,
                email_sent=False,
            ).exclude(email="")
            for guest in guests:
                # Flag first (guards duplicate sends across overlapping runs),
                # then send synchronously so delivery completes before exit.
                guest.email_sent = True
                guest.save(update_fields=["email_sent"])
                if send_roll_email_sync(guest):
                    sent += 1
        self.stdout.write(f"Emailed {sent} guest(s).")
