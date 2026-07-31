import secrets

from django.conf import settings
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from . import throttle
from .context_processors import LANG_COOKIE, SUPPORTED_LANGS
from .i18n import get_strings
from .metrics import (
    capture_errors,
    capture_gated,
    joins,
    photos_captured,
    upload_bytes,
)
from .models import Event, Guest, Photo
from .phones import normalize_phone

SESSION_KEY = (
    "guest:{slug}"  # per-event guest token, so one browser can join many events
)


def _session_guest(request, event):
    """Return the Guest tied to this session for this event, or None."""
    token = request.session.get(SESSION_KEY.format(slug=event.slug))
    if not token:
        return None
    return Guest.objects.filter(event=event, token=token).first()


def _t(request):
    """UI strings for this request — same language the templates get."""
    lang = request.COOKIES.get(LANG_COOKIE)
    if lang not in SUPPORTED_LANGS:
        lang = getattr(settings, "APP_LANG", "fa")
    return get_strings(lang)


def _join_context(request, event, **extra):
    """Join-page context. The `{n}` copy needs the roll size interpolated, and
    both the GET and the validation-error render need it."""
    t = _t(request)
    n = str(event.roll_size)
    ctx = {
        "event": event,
        "join_explain": t["join_explain"].replace("{n}", n),
        "join_step_2": t["join_step_2"].replace("{n}", n),
    }
    ctx.update(extra)
    return ctx


@require_http_methods(["GET"])
def landing(request):
    """Public marketing home at the site root."""
    return render(request, "snap/landing.html")


@require_http_methods(["GET"])
def metrics(request):
    """Prometheus scrape endpoint.

    **Not public.** Guest counts, event slugs and storage figures are operational
    data, and an unauthenticated metrics endpoint is also a free DoS amplifier
    (every scrape walks the media tree). Access needs the `METRICS_TOKEN`
    secret as a bearer token or `?token=`.

    Fails *closed*: with no token configured the endpoint 404s in production, so
    forgetting to set it cannot silently expose the data. In DEBUG it stays open
    for convenience. nginx also blocks the path publicly (defence in depth, see
    deploy/nginx-quicksnap.conf) — this check is what protects it if the app is
    ever reached directly.
    """
    from django.http import HttpResponse, HttpResponseNotFound
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    from .metrics import registry

    expected = getattr(settings, "METRICS_TOKEN", "")
    if not expected:
        if not settings.DEBUG:
            return HttpResponseNotFound()
    else:
        header = request.META.get("HTTP_AUTHORIZATION", "")
        supplied = (
            header[7:]
            if header.lower().startswith("bearer ")
            else request.GET.get("token", "")
        )
        # Constant-time compare: a plain `!=` leaks the token a character at a
        # time to anyone who can measure response latency.
        if not supplied or not secrets.compare_digest(supplied, expected):
            return HttpResponseNotFound()

    return HttpResponse(
        generate_latest(registry()),
        content_type=CONTENT_TYPE_LATEST,
    )


@require_POST
def set_language(request):
    """Persist the visitor's UI language in a cookie, then bounce back.

    Works with no JavaScript: the header toggle is a tiny POST form. We only
    redirect to same-site URLs so the `next` field can't be used for open
    redirects.
    """
    lang = request.POST.get("lang", "en")
    if lang not in SUPPORTED_LANGS:
        lang = "en"

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = "/"

    resp = HttpResponseRedirect(next_url)
    resp.set_cookie(LANG_COOKIE, lang, max_age=365 * 24 * 3600, samesite="Lax")
    return resp


@require_http_methods(["GET"])
def events(request):
    """Public index of joinable events, so guests without a QR can find them."""
    now = timezone.now()
    qs = Event.objects.filter(is_active=True).order_by("start_at", "name")
    visible = [e for e in qs if not e.has_ended]  # hide finished events

    def status(e):
        if not e.has_started:
            return "soon"
        return "open"

    items = [{"event": e, "status": status(e)} for e in visible]
    return render(request, "snap/events.html", {"items": items, "now": now})


@require_http_methods(["GET", "POST"])
def join(request, slug):
    event = get_object_or_404(Event, slug=slug)

    # Already joined this event in this session → straight to camera.
    existing = _session_guest(request, event)
    if existing and request.method == "GET":
        return redirect("snap:camera", slug=slug)

    if request.method == "POST":
        password = request.POST.get("password", "").strip()
        name = request.POST.get("name", "").strip()
        phone_raw = request.POST.get("phone", "").strip()

        t = _t(request)

        # Checked before anything else, and crucially before check_password():
        # the PBKDF2 hash is the expensive part we're protecting, so a blocked
        # client must be turned away without paying for it.
        if throttle.is_blocked(request, event):
            joins.labels(result="throttled").inc()
            return render(
                request,
                "snap/join.html",
                _join_context(
                    request,
                    event,
                    errors=[t["err_too_many"]],
                    name=name,
                    phone=phone_raw,
                ),
                status=429,
            )

        errors = []
        if not event.is_active or event.has_ended:
            errors.append(t["err_event_closed"])
        if not name:
            errors.append(t["err_name_required"])

        phone = normalize_phone(phone_raw)
        if not phone:
            errors.append(t["err_phone_invalid"])

        # Open events (no password set) accept anyone with the link.
        if event.requires_password and not event.check_password(password):
            errors.append(t["err_wrong_password"])

        if errors:
            joins.labels(result="rejected").inc()
            # Count it, and if this failure trips the limit say so immediately
            # rather than letting the next attempt deliver the news.
            if throttle.record_failure(request, event):
                errors = [t["err_too_many"]]
            return render(
                request,
                "snap/join.html",
                _join_context(
                    request, event, errors=errors, name=name, phone=phone_raw
                ),
                status=400,
            )

        # Success: forget this client's earlier fumbles, so a shared venue IP
        # doesn't accumulate strikes across a whole table of guests.
        throttle.clear(request, event)

        # Resume an existing roll (same phone) or start a fresh one.
        guest, _created = Guest.objects.get_or_create(
            event=event, phone=phone, defaults={"name": name}
        )
        joins.labels(result="created" if _created else "resumed").inc()
        # If returning (not created), the name might have changed — update it.
        if not _created and guest.name != name:
            guest.name = name
            guest.save(update_fields=["name"])

        request.session[SESSION_KEY.format(slug=event.slug)] = str(guest.token)
        return redirect("snap:camera", slug=slug)

    return render(request, "snap/join.html", _join_context(request, event))


@require_http_methods(["GET"])
def camera(request, slug):
    event = get_object_or_404(Event, slug=slug)
    guest = _session_guest(request, event)
    if guest is None:
        return redirect("snap:join", slug=slug)
    if not event.is_active or event.has_ended:
        return redirect("snap:done", slug=slug)
    if guest.roll_full:
        return redirect("snap:done", slug=slug)
    return render(
        request,
        "snap/camera.html",
        {"event": event, "guest": guest, "remaining": guest.remaining},
    )


@require_POST
def capture(request, slug):
    event = get_object_or_404(Event, slug=slug)
    guest = _session_guest(request, event)
    if guest is None:
        capture_errors.labels(reason="not_joined").inc()
        return JsonResponse({"error": "not_joined"}, status=403)

    # Time window + active gate (never trust the client). Gating is the app
    # working correctly, so it is counted separately from real errors.
    if not event.is_open:
        reason = (
            "ended"
            if event.has_ended
            else ("not_started" if not event.has_started else "closed")
        )
        capture_gated.labels(reason=reason).inc()
        return JsonResponse({"error": reason}, status=403)

    image = request.FILES.get("image")
    if not image:
        capture_errors.labels(reason="no_image").inc()
        return JsonResponse({"error": "no_image"}, status=400)

    # Server-side roll cap — never trust the client counter. Locking the guest
    # row makes the check-then-insert atomic: two uploads racing (double tap,
    # or a snap and a gallery pick in flight together) can't both slip past a
    # roll_size-1 count and overshoot the roll.
    with transaction.atomic():
        locked = Guest.objects.select_for_update().get(pk=guest.pk)
        if locked.roll_full:
            # A guest finishing their roll is the expected end state, not a fault.
            capture_gated.labels(reason="roll_full").inc()
            return JsonResponse({"remaining": 0, "done": True}, status=409)
        Photo.objects.create(guest=locked, image=image)
        remaining = locked.remaining

    photos_captured.labels(event=event.slug).inc()
    upload_bytes.observe(image.size)
    return JsonResponse({"remaining": remaining, "done": remaining == 0})


@require_http_methods(["GET"])
def done(request, slug):
    event = get_object_or_404(Event, slug=slug)
    guest = _session_guest(request, event)
    t = _t(request)

    # Persian word order won't survive being split into pre/post fragments
    # around the number, so the copy is one string with {n}/{event} holes and
    # we fill them here — Django templates can't do string replace.
    taken = guest.taken if guest else 0
    ctx = {
        "event": event,
        "guest": guest,
        "done_frames": t["done_frames"]
        .replace("{n}", str(taken))
        .replace("{event}", event.name),
        "done_thanks": t["done_thanks"].replace("{event}", event.name),
        # One lit frame per photo, staggered. Capped so a 40-photo roll doesn't
        # turn into a wall of blinking squares.
        "photo_slots": [600 + i * 45 for i in range(min(taken, 12))],
    }
    return render(request, "snap/done.html", ctx)
