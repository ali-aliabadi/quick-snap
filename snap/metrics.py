"""Prometheus metrics for Quick Snap.

Two kinds of signal live here:

* **Event counters/histograms** — incremented inline by the views and the
  request middleware (requests, uploads, capture rejections, joins).
* **State gauges** — *not* tracked incrementally. `StateCollector` reads them
  from the database at scrape time, so they can never drift out of sync with
  reality the way a long-lived gauge does, and they sidestep multiprocess gauge
  semantics entirely.

**Multiprocess.** gunicorn runs several workers, each with its own memory, so a
scrape would otherwise hit one worker at random and report a third of the
traffic. `prometheus_client` handles this by having every worker write to files
in ``PROMETHEUS_MULTIPROC_DIR``, which the scrape then aggregates. That env var
is set in the container (see docker-compose.prod.yml) and the directory is
wiped on boot by deploy/entrypoint.sh, because files left by dead workers would
otherwise be counted forever.
"""

import os

from prometheus_client import CollectorRegistry, Counter, Histogram, multiprocess
from prometheus_client.core import GaugeMetricFamily

MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR")

# --------------------------------------------------------------------------- #
# Event metrics
# --------------------------------------------------------------------------- #
# `view` is the resolved Django view name, never the raw path — a raw path would
# let 404 scanners invent unbounded label values and blow up cardinality.
http_requests = Counter(
    "quicksnap_http_requests_total",
    "HTTP requests by view, method and status class.",
    ["view", "method", "status"],
)

http_latency = Histogram(
    "quicksnap_http_request_duration_seconds",
    "Request latency by view.",
    ["view"],
    # Tuned for this app: most requests are small template renders, but
    # `capture` carries a multi-MB JPEG, so the tail matters.
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

photos_captured = Counter(
    "quicksnap_photos_captured_total",
    "Photos successfully committed to a roll.",
    ["event"],
)

capture_errors = Counter(
    "quicksnap_capture_errors_total",
    "Capture attempts that genuinely failed, by reason (not_joined, no_image).",
    ["reason"],
)

# Rejections that are the app working *correctly*: the roll is finished, or the
# event's time window is closed. Kept separate from capture_errors so a healthy
# event full of guests finishing their rolls doesn't look like an outage on the
# dashboard — and so the success-rate gauge isn't dragged down by normal gating.
capture_gated = Counter(
    "quicksnap_capture_gated_total",
    "Capture attempts refused by design, by reason "
    "(roll_full, not_started, ended, closed).",
    ["reason"],
)

upload_bytes = Histogram(
    "quicksnap_upload_bytes",
    "Size of accepted photo uploads, in bytes.",
    buckets=(
        256_000,
        512_000,
        1_000_000,
        2_000_000,
        3_000_000,
        5_000_000,
        8_000_000,
        12_000_000,
        15_000_000,
    ),
)

joins = Counter(
    "quicksnap_joins_total",
    "Join attempts by outcome (created, resumed, rejected, throttled).",
    ["result"],
)


# --------------------------------------------------------------------------- #
# State gauges — read from the DB on each scrape
# --------------------------------------------------------------------------- #
class StateCollector:
    """Business state, queried at scrape time rather than tracked.

    Cheap at this scale (a handful of COUNTs over thousands of rows) and it
    cannot go stale. Any failure here must not break the scrape — a monitoring
    endpoint that 500s during an incident is worse than a missing panel — so
    everything is wrapped and partial results are returned on error.
    """

    def collect(self):
        try:
            from django.db.models import Count

            from .models import Event, Guest, Photo

            events = GaugeMetricFamily(
                "quicksnap_events",
                "Events by state.",
                labels=["state"],
            )
            active = Event.objects.filter(is_active=True)
            open_now = [e for e in active if e.is_open]
            upcoming = [e for e in active if not e.has_started]
            events.add_metric(["open"], len(open_now))
            events.add_metric(["upcoming"], len(upcoming))
            events.add_metric(["total"], Event.objects.count())
            yield events

            yield GaugeMetricFamily(
                "quicksnap_guests",
                "Guests who have joined a roll (all events).",
                value=Guest.objects.count(),
            )
            yield GaugeMetricFamily(
                "quicksnap_photos",
                "Photos stored (all events).",
                value=Photo.objects.count(),
            )

            # Per-event fill: how much of each roll the guests have used. This is
            # the number a host actually watches during an event.
            fill = GaugeMetricFamily(
                "quicksnap_event_roll_fill_ratio",
                "Photos taken / photos available, per open event (0..1).",
                labels=["event"],
            )
            photos = GaugeMetricFamily(
                "quicksnap_event_photos",
                "Photos taken per open event.",
                labels=["event"],
            )
            guests_per = GaugeMetricFamily(
                "quicksnap_event_guests",
                "Guests joined per open event.",
                labels=["event"],
            )
            for ev in open_now:
                agg = ev.guests.aggregate(n=Count("photos"))
                taken = agg["n"] or 0
                n_guests = ev.guests.count()
                capacity = n_guests * ev.roll_size
                photos.add_metric([ev.slug], taken)
                guests_per.add_metric([ev.slug], n_guests)
                fill.add_metric([ev.slug], (taken / capacity) if capacity else 0.0)
            yield photos
            yield guests_per
            yield fill

            yield from self._storage()
        except Exception:  # noqa: BLE001 — a scrape must never raise
            return

    def _storage(self):
        """Disk footprint: the SQLite file and the media tree.

        The media tree is the one that grows without bound during an event, and
        running the VPS out of disk mid-wedding loses photos.
        """
        from django.conf import settings

        db = settings.DATABASES.get("default", {}).get("NAME")
        if db and isinstance(db, (str, os.PathLike)) and os.path.exists(db):
            yield GaugeMetricFamily(
                "quicksnap_database_bytes",
                "Size of the SQLite database file.",
                value=os.path.getsize(db),
            )

        root = getattr(settings, "MEDIA_ROOT", None)
        if root and os.path.isdir(root):
            total = 0
            count = 0
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, f))
                        count += 1
                    except OSError:
                        continue
            yield GaugeMetricFamily(
                "quicksnap_media_bytes",
                "Total size of stored photos on local disk.",
                value=total,
            )
            yield GaugeMetricFamily(
                "quicksnap_media_files",
                "Number of photo files on local disk.",
                value=count,
            )


def registry():
    """Registry to serve a scrape from.

    In multiprocess mode the per-worker counter files are aggregated into a
    fresh registry per scrape. `StateCollector` is attached in both modes — it
    queries the DB live, so it belongs to the scrape rather than to a worker.
    """
    if MULTIPROC_DIR:
        reg = CollectorRegistry()
        multiprocess.MultiProcessCollector(reg)
        reg.register(StateCollector())
        return reg

    from prometheus_client import REGISTRY

    # Single-process (dev, tests): register once on the global registry, since
    # re-registering the same collector raises a duplicate-timeseries error.
    global _state_registered
    if not _state_registered:
        REGISTRY.register(StateCollector())
        _state_registered = True
    return REGISTRY


_state_registered = False
