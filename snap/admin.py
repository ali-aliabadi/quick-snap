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
        help_text="Set/reset the guest password. Leave blank to keep the current one.",
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
        ]

    def save(self, commit=True):
        event = super().save(commit=False)
        raw = self.cleaned_data.get("password")
        if raw:
            event.set_password(raw)
        if commit:
            event.save()
        return event

    def clean(self):
        cleaned = super().clean()
        # New event must have a password.
        if not self.instance.pk and not cleaned.get("password"):
            self.add_error("password", "A password is required for a new event.")
        return cleaned


def download_all_photos(modeladmin, request, queryset):
    """Admin action: stream a ZIP of every photo across the selected events."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for event in queryset:
            for guest in event.guests.all():
                for i, photo in enumerate(guest.photos.all(), start=1):
                    photo.image.open("rb")
                    try:
                        data = photo.image.read()
                    finally:
                        photo.image.close()
                    safe_name = (
                        "".join(
                            c if c.isalnum() or c in "-_ " else "_" for c in guest.name
                        ).strip()
                        or f"guest{guest.pk}"
                    )
                    zf.writestr(f"{event.slug}/{safe_name}/{i:03d}.jpg", data)
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
    list_display = ["name", "email", "event", "taken", "email_sent", "created_at"]
    list_filter = ["event", "email_sent"]
    search_fields = ["name", "email"]
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
