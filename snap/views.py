from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .emails import send_roll_email
from .models import Event, Guest, Photo

SESSION_KEY = (
    "guest:{slug}"  # per-event guest token, so one browser can join many events
)


def _session_guest(request, event):
    """Return the Guest tied to this session for this event, or None."""
    token = request.session.get(SESSION_KEY.format(slug=event.slug))
    if not token:
        return None
    return Guest.objects.filter(event=event, token=token).first()


@require_http_methods(["GET"])
def landing(request):
    """Public marketing home at the site root."""
    return render(request, "snap/landing.html")


@require_http_methods(["GET", "POST"])
def join(request, slug):
    event = get_object_or_404(Event, slug=slug)

    # Already joined this event in this session → straight to camera.
    existing = _session_guest(request, event)
    if existing and request.method == "GET":
        return redirect("snap:camera", slug=slug)

    if request.method == "POST":
        password = request.POST.get("password", "")
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()

        errors = []
        if not event.is_active or event.has_ended:
            errors.append("This event is closed.")
        if not name:
            errors.append("Please enter your name.")
        if not event.check_password(password):
            errors.append("Wrong event password.")

        if errors:
            return render(
                request,
                "snap/join.html",
                {"event": event, "errors": errors, "name": name, "email": email},
                status=400,
            )

        # Resume an existing roll (same name+email) or start a fresh one.
        guest, _created = Guest.objects.get_or_create(
            event=event, name=name, email=email
        )
        request.session[SESSION_KEY.format(slug=event.slug)] = str(guest.token)
        return redirect("snap:camera", slug=slug)

    return render(request, "snap/join.html", {"event": event})


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
        return JsonResponse({"error": "not_joined"}, status=403)

    # Time window + active gate (never trust the client).
    if not event.is_open:
        reason = (
            "ended"
            if event.has_ended
            else ("not_started" if not event.has_started else "closed")
        )
        return JsonResponse({"error": reason}, status=403)

    # Server-side roll cap — never trust the client counter.
    if guest.roll_full:
        return JsonResponse({"remaining": 0, "done": True}, status=409)

    image = request.FILES.get("image")
    if not image:
        return JsonResponse({"error": "no_image"}, status=400)

    Photo.objects.create(guest=guest, image=image)

    remaining = guest.remaining
    done = remaining == 0
    if done and guest.email and not guest.email_sent:
        # Flag first (guards duplicates), then send in a background thread.
        guest.email_sent = True
        guest.save(update_fields=["email_sent"])
        send_roll_email(guest)

    return JsonResponse({"remaining": remaining, "done": done})


@require_http_methods(["GET"])
def done(request, slug):
    event = get_object_or_404(Event, slug=slug)
    guest = _session_guest(request, event)
    return render(request, "snap/done.html", {"event": event, "guest": guest})
