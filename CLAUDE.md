# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Quick Snap is a QR-driven "disposable camera" webapp for events. A guest scans a QR → `/e/<slug>/`, enters the event password + name (email optional), and gets a fixed roll of **N** instant photos: tap to snap, no review, no retake. When the roll fills, guests who gave an email are emailed their own photos; the host collects **all** photos via Django admin. Django + uv, SQLite metadata, photos to local disk (dev) or S3 (prod), live camera via `getUserMedia`.

## Commands

Development is driven through the `Makefile` (targets wrap `uv run`):

```bash
make lint          # ruff + black --check + isort --check (no writes)
make format        # isort + black + ruff --fix (rewrites files)
make test          # Django test suite (uv run python manage.py test)
make check         # lint + test
make migrate / make makemigrations
make runserver
```

Run a single test by path: `uv run python manage.py test snap.tests.<Class>.<method>`.

There is no separate test runner — everything is Django's `TestCase` in `snap/tests.py`.

## Architecture

Single Django app `snap` under the `quicksnap` project. The guest flow is four views in `snap/views.py`, all keyed off the URL `<slug>` and a per-event session token:

- **join** → **camera** → **capture** (POST, one photo) → **done**. Session key is `guest:<slug>`, holding the `Guest.token`, so one browser can hold rolls for many events independently.
- Returning-guest resume works via `Guest.get_or_create(event, name, email)` plus a `UniqueConstraint(event, name, email)` — re-entering the same name+email resumes the same roll rather than starting over. Sessions live 8h (`SESSION_COOKIE_AGE`) to survive a long event.

**Trust boundary:** the client counter is never trusted. `capture` re-checks `event.is_open` (active + within `start_at`/`end_at` window) and `guest.roll_full` server-side, returning JSON error codes (`not_started`, `ended`, `closed`, 409 when the roll is full). Any gating logic must be enforced here, not just in templates/JS.

**Models** (`snap/models.py`): `Event` (hashed `password_hash` via `set_password`/`check_password`, `roll_size`, optional `start_at`/`end_at`, computed `is_open`/`has_started`/`has_ended`) → `Guest` (`token`, `taken`/`remaining`/`roll_full` derived from photo count) → `Photo` (`ImageField`, uploaded to `media/<slug>/<guest-token>/<uuid>.jpg` via `photo_upload_path`).

**Email** (`snap/emails.py`): when the final photo lands, `capture` sets `guest.email_sent = True` *first* (dedup guard), then `send_roll_email` sends attachments in a **daemon thread** so the request returns instantly. On failure the worker unflags `email_sent` for retry and must never raise out of the thread.

**i18n** (`snap/i18n.py` + `context_processors.py`): guest-facing UI strings are a plain dict, not gettext/.mo. Language is the `APP_LANG` setting (`fa` default, RTL Persian / `en`). The `ui_strings` context processor exposes `t`, `lang`, `rtl` to every template — add new UI copy as keys in **both** `fa` and `en` in `STRINGS`.

**Admin** (`snap/admin.py`) is the host's control panel: `EventAdminForm` adds a write-only password field (hashes into `password_hash`); the `download_all_photos` action streams a ZIP grouped by event/guest. This is how hosts create events and collect photos — there is no separate host UI.

## Config & environment

Settings are entirely env-driven (`django-environ`, reads `.env`). Key flags: `USE_S3` switches photo storage (local `MEDIA_ROOT` ↔ S3 via `django-storages`) with no code change; `DEBUG=False` turns on secure-cookie/SSL-proxy settings for prod behind Caddy. `.env.example` is dev-ready as-is (local storage, console email backend that prints to terminal).

**Dev camera note:** browsers only allow `getUserMedia` in a secure context, so use `http://localhost:8000` (treated as secure) — **not** `127.0.0.1`. Prod requires real HTTPS (Caddy, see `deploy/`).

**SQLite concurrency:** WAL mode + `synchronous=NORMAL` are enabled on every connection in `SnapConfig.ready` (`snap/apps.py`), and a 20s busy `timeout` is set in settings, because concurrent guests plus background email threads otherwise hit "database is locked".

## Deployment

`docker-compose.prod.yml` runs gunicorn (`Dockerfile`) behind Caddy for automatic HTTPS; `deploy/` holds `Caddyfile`, `entrypoint.sh` (migrate + collectstatic on boot), and the systemd unit. `make docker-*` targets wrap the compose file.
