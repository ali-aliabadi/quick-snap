# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Quick Snap is a QR-driven "disposable camera" webapp for events. A guest scans a QR → `/e/<slug>/`, enters the event password + name + **mobile number**, and gets a fixed roll of **N** instant photos: tap to snap or pick from their gallery, no review, no retake. The host collects **all** photos via Django admin — there is no guest-facing delivery (no email); the host thanks guests out of band. Django + uv, SQLite metadata, photos to local disk (dev) or S3 (prod), live camera via `getUserMedia`.

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
- **Phone is the roll identity.** Returning-guest resume works via `Guest.get_or_create(event, phone)` plus a `UniqueConstraint(event, phone)` — re-entering the same number resumes the same roll rather than starting over, so it's **one roll per phone number per event**. Numbers are normalized to a canonical `09xxxxxxxxx` by `snap/phones.py` (`normalize_phone` accepts `+98…`/`0098…`/`9…` and Persian/Arabic-Indic digits) — always normalize before querying, or the same person gets two rolls. Sessions live 8h (`SESSION_COOKIE_AGE`) to survive a long event.

**Trust boundary:** the client counter is never trusted. `capture` re-checks `event.is_open` (active + within `start_at`/`end_at` window) and `guest.roll_full` server-side, returning JSON error codes (`not_started`, `ended`, `closed`, 409 when the roll is full). Any gating logic must be enforced here, not just in templates/JS.

**Models** (`snap/models.py`): `Event` (hashed `password_hash` via `set_password`/`check_password`, `roll_size`, optional `start_at`/`end_at`, computed `is_open`/`has_started`/`has_ended`) → `Guest` (`name`, `phone`, `token`, `taken`/`remaining`/`roll_full` derived from photo count) → `Photo` (`ImageField`, uploaded to `media/<slug>/<guest-token>/<uuid>.jpg` via `photo_upload_path`).

**No email.** Photos are never delivered to guests — the host collects everything from admin. Guests that predate the phone migration carry a `legacy-<pk>` placeholder in `phone` (see migration `0002`); it's intentionally not a valid mobile so it can't collide with a real number.

**Camera** (`snap/templates/snap/camera.html`): one self-contained template — live `getUserMedia` preview, `ImageCapture` full-res still with a canvas fallback, plus a **gallery picker** (`#file`, no `capture` attribute so it opens the photo library). Both paths funnel through `submitBlob`, so a gallery pick consumes an exposure exactly like a live snap. The shutter is deliberately **white, not red** — a red circle reads as "recording video" and guests kept asking if the app was filming them.

**i18n** (`snap/i18n.py` + `context_processors.py`): guest-facing UI strings are a plain dict, not gettext/.mo. Language is the `APP_LANG` setting (**`fa` default**, RTL Persian / `en`), overridden per-visitor by the `ui_lang` cookie from the header toggle. The `ui_strings` context processor exposes `t`, `lang`, `rtl` to every template — add new UI copy as keys in **both** `fa` and `en` in `STRINGS` (a test asserts key parity). Copy containing `{n}` is interpolated in the view (`_join_context`), since Django templates can't do string replace.

**Admin** (`snap/admin.py`) is the host's control panel: `EventAdminForm` adds a write-only password field (hashes into `password_hash`); the `download_all_photos` action streams a ZIP grouped by event/guest. This is how hosts create events and collect photos — there is no separate host UI.

## Config & environment

Settings are entirely env-driven (`django-environ`, reads `.env`). Key flags: `USE_S3` switches photo storage (local `MEDIA_ROOT` ↔ S3 via `django-storages`) with no code change; `DEBUG=False` turns on secure-cookie/SSL-proxy settings for prod behind nginx. `.env.example` is dev-ready as-is (local storage).

**Dev camera note:** browsers only allow `getUserMedia` in a secure context, so use `http://localhost:8000` (treated as secure) — **not** `127.0.0.1`. Prod requires real HTTPS (nginx, see `deploy/`).

**SQLite concurrency:** WAL mode + `synchronous=NORMAL` are enabled on every connection in `SnapConfig.ready` (`snap/apps.py`), and a 20s busy `timeout` is set in settings, so concurrent guests don't hit "database is locked".

## Deployment

`docker-compose.prod.yml` runs gunicorn (`Dockerfile`) behind the host's nginx for HTTPS; `deploy/` holds `nginx-quicksnap.conf`, `entrypoint.sh` (migrate + collectstatic on boot), and the systemd unit. `make docker-*` targets wrap the compose file.
