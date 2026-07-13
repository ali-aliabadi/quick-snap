# Quick Snap

QR-driven "disposable camera" webapp for events. A guest scans a QR code, enters the
event password + their name (email optional), then gets a fixed roll of **N instant
photos** — tap to snap, no review, no retake. When the roll fills up, if they gave an
email, they're emailed their own photos. The host collects **all** photos centrally.
Multiple events are supported, each with its own password and roll size.

Stack: Django + uv, SQLite metadata, photos to **local disk (dev)** or **S3 (prod)**,
live browser camera via `getUserMedia`.

## Guest flow

1. Scan QR → `/e/<slug>/`
2. Enter event password + name (+ optional email)
3. Live camera opens → tap to snap, up to N photos, no retakes
4. Roll full → thank-you page; if email given, photos are emailed as attachments

Returning guests: sessions live 8h and, if the cookie is lost, re-entering the **same
name + email** resumes the same roll instead of starting over — safe across a long event.

## Scheduling (optional start / end times)

An event can have an optional **start** and **end** time (set in admin). Guests see a live
countdown — "Starts in…" before it opens, "Ends in…" while it's running — on both the join
screen and the camera. Snapping is blocked (server-side too) before the start and after the
end. Leave both blank for an always-open event.

## Local development

```bash
uv sync                                   # install deps into .venv
cp .env.example .env                      # defaults are dev-ready (local storage, console email)
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py runserver
```

Then in the Django admin (`http://localhost:8000/admin/`) create an **Event**: set a
name, slug, roll size (N), and password. Guests join at:

```
http://localhost:8000/e/<slug>/
```

Use `localhost` (not `127.0.0.1`) — browsers treat `localhost` as a **secure context**,
so the camera works without HTTPS in dev. Email prints to the terminal (console backend).

## Creating a QR code

The QR just encodes the join URL: `https://<your-domain>/e/<slug>/`. Paste that into any
QR generator and print it for the event.

## Production (VPS)

1. Put the app in `/srv/quicksnap`, install [uv](https://docs.astral.sh/uv/).
2. Create `/srv/quicksnap/.env` from `.env.example` and set:
   - `DEBUG=False`, `SECRET_KEY=<random>`, `ALLOWED_HOSTS=snap.yourdomain.com`
   - `CSRF_TRUSTED_ORIGINS=https://snap.yourdomain.com`
   - `USE_S3=True` + your S3 credentials/bucket
   - SMTP email settings (`EMAIL_BACKEND=...smtp...`, host, user, password)
3. Build + migrate:
   ```bash
   uv sync
   uv run manage.py migrate
   uv run manage.py collectstatic --noinput
   uv run manage.py createsuperuser
   ```
4. Reverse proxy + HTTPS with **Caddy** (`deploy/Caddyfile`) — required for the camera.
5. Run gunicorn via **systemd** (`deploy/quicksnap.service`).

## Collecting photos (host)

- Django admin → **Events** → select your event → action **"Download all photos as ZIP"**
  gives you every photo, grouped by guest.
- Individual guests/photos are browsable with thumbnails under **Guests** / **Photos**.

## Configuration reference

See `.env.example` — every setting is env-driven (secret key, hosts, database, storage
backend, email). The `USE_S3` flag alone switches photo storage between local disk and S3.

