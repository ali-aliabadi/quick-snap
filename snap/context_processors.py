from django.conf import settings

from .i18n import get_strings

SUPPORTED_LANGS = ("en", "fa")
LANG_COOKIE = "ui_lang"


def ui_strings(request):
    """Expose translated UI strings + direction to every template.

    Language precedence: per-visitor `ui_lang` cookie (set by the header toggle)
    → the APP_LANG setting → English. English is the default so the site opens in
    English for everyone, while Persian stays one tap away in the header.
    """
    lang = request.COOKIES.get(LANG_COOKIE)
    if lang not in SUPPORTED_LANGS:
        lang = getattr(settings, "APP_LANG", "fa")
    s = get_strings(lang)
    return {"t": s, "lang": s["lang"], "rtl": s["dir"] == "rtl"}
