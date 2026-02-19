# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="neel.pullikol@gmail.com"
LABEL description="StatusWatch — event-driven webhook receiver"

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source — explicitly owned by appuser so permission
# mismatches on the host (e.g. 600 files) don't cause PermissionError
COPY --chown=appuser:appgroup app/ ./app/

# Drop to non-root
USER appuser

# Uvicorn listens on this port; expose it for documentation
EXPOSE 8000

# Graceful shutdown: uvicorn handles SIGTERM cleanly.
# PORT and WEB_CONCURRENCY are injected by Render/Railway automatically.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --log-level info --access-log"]
