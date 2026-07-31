import zipfile
from io import BytesIO

from django import forms
from django.contrib import admin
from django.http import HttpResponse
from django.utils.html import format_html

from .models import Event, Guest, Photo


class EventAdminForm(forms.ModelForm):
    """Adds a write-only password field that hashes into Event.password_hash."""

    password = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text=(
            "Optional. Leave blank on a new event to let anyone with the link "
            "join. On an existing event, blank keeps the current password — "
            "tick 'Remove password' to clear it."
        ),
    )
    remove_password = forms.BooleanField(
        required=False,
        help_text="Clear the password so anyone with the link can join.",
    )

    class Meta:
        model = Event
        fields = [
            "name",
            "slug",
            "roll_size",
            "start_at",
            "end_at",
            "is_active",
            "password",
            "remove_password",
        ]

    def save(self, commit=True):
        event = super().save(commit=False)
        raw = self.cleaned_data.get("password")
        if self.cleaned_data.get("remove_password"):
            event.set_password("")  # open event
        elif raw:
            event.set_password(raw)
        if commit:
            event.save()
        return event

    def clean(self):
        cleaned = super().clean()
        # Password is optional; an event with none is simply open to anyone with
        # the link. Only guard against the contradictory combination.
        if cleaned.get("remove_password") and cleaned.get("password"):
            self.add_error(
                "remove_password",
                "Either set a new password or remove it — not both.",
            )
        return cleaned


def download_all_photos(modeladmin, request, queryset):
    """Admin action: stream a ZIP of every photo across the selected events."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for event in queryset:
            for guest in event.guests.all():
                # Two guests can share a name (only phone is unique), so the
                # phone is part of the folder — otherwise their photos would
                # collide and overwrite inside the ZIP.
                safe_name = "".join(
                    c if c.isalnum() or c in "-_ " else "_" for c in guest.name
                ).strip()
                folder = f"{safe_name or 'guest'}-{guest.phone or guest.pk}"
                for i, photo in enumerate(guest.photos.all(), start=1):
                    photo.image.open("rb")
                    try:
                        data = photo.image.read()
                    finally:
                        photo.image.close()
                    zf.writestr(f"{event.slug}/{folder}/{i:03d}.jpg", data)
    buffer.seek(0)
    resp = HttpResponse(buffer.getvalue(), content_type="application/zip")
    resp["Content-Disposition"] = 'attachment; filename="quicksnap_photos.zip"'
    return resp


download_all_photos.short_description = "Download all photos as ZIP"


class PhotoInline(admin.TabularInline):
    model = Photo
    extra = 0
    readonly_fields = ["thumb", "created_at"]
    fields = ["thumb", "created_at"]

    def thumb(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="height:80px;border-radius:4px;" />', obj.image.url
            )
        return "—"

    thumb.short_description = "Preview"


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    form = EventAdminForm
    list_display = [
        "name",
        "slug",
        "roll_size",
        "start_at",
        "end_at",
        "is_active",
        "guest_count",
        "created_at",
    ]
    prepopulated_fields = {"slug": ("name",)}
    actions = [download_all_photos]

    def guest_count(self, obj):
        return obj.guests.count()

    guest_count.short_description = "Guests"


@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = ["name", "phone", "event", "taken", "created_at"]
    list_filter = ["event"]
    search_fields = ["name", "phone"]
    inlines = [PhotoInline]
    readonly_fields = ["token", "created_at"]

    def taken(self, obj):
        return f"{obj.taken}/{obj.event.roll_size}"

    taken.short_description = "Roll"


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ["id", "guest", "thumb", "created_at"]
    list_filter = ["guest__event"]
    readonly_fields = ["thumb", "created_at"]

    def thumb(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="height:60px;border-radius:4px;" />', obj.image.url
            )
        return "—"

    thumb.short_description = "Preview"
