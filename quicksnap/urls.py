from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from snap import views as snap_views

urlpatterns = [
    path("", snap_views.landing, name="landing"),
    path("events/", snap_views.events, name="events"),
    path("set-language/", snap_views.set_language, name="set_language"),
    path("metrics", snap_views.metrics, name="metrics"),
    path("admin/", admin.site.urls),
    path("e/", include("snap.urls")),
]

# Serve uploaded photos locally in dev (S3 serves its own URLs in prod).
if getattr(settings, "MEDIA_URL", None) and getattr(settings, "MEDIA_ROOT", None):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
