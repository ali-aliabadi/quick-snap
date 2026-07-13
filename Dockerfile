# Production image for Quick Snap.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (cached layer). Only prod deps, no dev group.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project --frozen 2>/dev/null || uv sync --no-dev --no-install-project

# App code
COPY . .

# Put the venv on PATH so `python`/`gunicorn` resolve without `uv run`.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Migrate + collectstatic on boot, then serve. Invoked via `sh` so the script
# does not need the executable bit set on the host.
ENTRYPOINT ["sh", "/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "quicksnap.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
