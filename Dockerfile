# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="you@example.com"
LABEL description="StatusWatch — event-driven Statuspage.io webhook receiver"

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app/ ./app/

# Drop to non-root
USER appuser

# Uvicorn listens on this port; expose it for documentation
EXPOSE 8000

# Graceful shutdown: uvicorn handles SIGTERM cleanly
CMD ["sh", "-c", "uvicorn app.main:app \
    --host 0.0.0.0 \
    --port ${PORT:-8000} \
    --workers 2 \
    --log-level info \
    --access-log"]
