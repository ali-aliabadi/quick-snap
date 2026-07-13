from django.conf import settings

from .i18n import get_strings


def ui_strings(request):
    """Expose translated UI strings + direction to every template."""
    lang = getattr(settings, "APP_LANG", "fa")
    s = get_strings(lang)
    return {"t": s, "lang": s["lang"], "rtl": s["dir"] == "rtl"}
