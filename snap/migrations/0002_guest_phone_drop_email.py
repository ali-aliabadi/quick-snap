"""Phone replaces email as the guest's roll identity.

Guests that predate this change have no phone number, so they get a unique
`legacy-<pk>` placeholder: their photos and rolls stay intact and browsable in
admin, they just can't resume a roll by entering a number.
"""

from django.db import migrations, models


def backfill_legacy_phones(apps, schema_editor):
    Guest = apps.get_model("snap", "Guest")
    for guest in Guest.objects.filter(phone=""):
        # Unique per row, and never a valid mobile — so it can't collide with a
        # real guest normalizing to the same value.
        Guest.objects.filter(pk=guest.pk).update(phone=f"legacy-{guest.pk}")


def unbackfill(apps, schema_editor):
    Guest = apps.get_model("snap", "Guest")
    Guest.objects.filter(phone__startswith="legacy-").update(phone="")


class Migration(migrations.Migration):
    dependencies = [("snap", "0001_initial")]

    operations = [
        # Drop the old identity constraint first — it names the email column.
        migrations.RemoveConstraint(
            model_name="guest",
            name="unique_guest_per_event",
        ),
        migrations.AddField(
            model_name="guest",
            name="phone",
            field=models.CharField(db_index=True, default="", max_length=20),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_legacy_phones, unbackfill),
        migrations.RemoveField(model_name="guest", name="email"),
        migrations.RemoveField(model_name="guest", name="email_sent"),
        migrations.AddConstraint(
            model_name="guest",
            constraint=models.UniqueConstraint(
                fields=("event", "phone"), name="unique_guest_phone_per_event"
            ),
        ),
    ]
