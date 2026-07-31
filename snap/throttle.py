"""Failed-join throttling.

Checking an event password costs a PBKDF2 hash (~80ms measured on the VPS). With
3 cores that means roughly 37 requests/second saturates the box, so an unthrottled
join form is both a password-guessing oracle *and* a cheap way to take the site
down mid-event. This module caps how fast one client can fail.

**Why only failures are counted.** Every guest at a wedding arrives through the
same venue NAT, and Cloudflare collapses them further, so a limit on *attempts*
would throttle a whole room the moment a few people fluff their number. A guest
who types their details correctly is never counted, no matter how many share the
IP; only wrong passwords and malformed numbers accrue.

**The limit is deliberately loose.** The event password is printed on the tables —
it is not a secret worth protecting to the last guess. The goal is to make
brute-forcing slow and to stop the CPU burn, while leaving enough headroom that a
confused table of guests never hits it. Hence a generous count over a short
window, and a cooldown measured in minutes rather than hours.

Not a security boundary: the client IP comes from headers Cloudflare sets, which a
direct-to-origin attacker could forge. Cloudflare's own rate limiting is the
robust layer; this is defence in depth plus CPU protection.
"""

from django.core.cache import cache

# Failures allowed per (IP, event) inside WINDOW before the cooldown applies.
MAX_FAILURES = 12
# Rolling window the failures are counted over, in seconds.
WINDOW = 300
# How long a client stays blocked once it trips the limit, in seconds.
COOLDOWN = 120


def client_ip(request):
    """Best-effort client IP.

    Order matters: this app sits behind Cloudflare *and* the host nginx, so
    REMOTE_ADDR is nginx and X-Real-IP is Cloudflare's edge. CF-Connecting-IP is
    the only header carrying the actual visitor, with XFF's first entry as the
    fallback for a direct origin hit.
    """
    cf = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
    if cf:
        return cf
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        # Left-most entry is the origin client; the rest are proxies.
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _keys(request, event):
    ip = client_ip(request)
    return (
        f"joinfail:{event.slug}:{ip}",
        f"joinblock:{event.slug}:{ip}",
    )


def is_blocked(request, event):
    """True if this client is in its cooldown for this event."""
    _, block_key = _keys(request, event)
    return cache.get(block_key) is not None


def record_failure(request, event):
    """Count one failed join. Returns True if this failure trips the cooldown.

    Uses add()+incr() so the TTL is set once when the window opens and is not
    refreshed by later failures — that keeps the window *rolling from the first
    failure* rather than extending every time, which would let a slow trickle
    of guesses keep a client blocked indefinitely.
    """
    fail_key, block_key = _keys(request, event)
    cache.add(fail_key, 0, WINDOW)
    try:
        count = cache.incr(fail_key)
    except ValueError:
        # The key expired between add() and incr(); treat as the first failure.
        cache.set(fail_key, 1, WINDOW)
        count = 1

    if count >= MAX_FAILURES:
        cache.set(block_key, True, COOLDOWN)
        cache.delete(fail_key)  # fresh count after the cooldown
        return True
    return False


def clear(request, event):
    """Forget a client's failures — called on a successful join, so one guest
    fumbling their number doesn't leave the rest of their table close to the
    limit on a shared IP."""
    fail_key, _ = _keys(request, event)
    cache.delete(fail_key)
