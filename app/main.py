"""
StatusWatch — FastAPI Webhook Receiver
Receives push notifications from Statuspage.io-powered status pages
(e.g. status.openai.com) and logs structured incident events to console.
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.models import ComponentUpdate, IncidentPayload, IncidentUpdate
from app.handlers import handle_incident, handle_component_update

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("statuswatch")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="StatusWatch",
    description="Event-driven Statuspage.io webhook receiver",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Optional HMAC signature verification
# Set STATUSPAGE_WEBHOOK_SECRET env var to enable.
# ---------------------------------------------------------------------------
WEBHOOK_SECRET = os.getenv("STATUSPAGE_WEBHOOK_SECRET", "")


def _verify_signature(body: bytes, signature_header: str | None) -> None:
    """Raise 401 if HMAC signature verification fails (when secret is set)."""
    if not WEBHOOK_SECRET:
        return  # verification disabled

    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Statuspage-Signature header",
        )

    expected = hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(f"sha256={expected}", signature_header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/webhook/statuspage", tags=["webhooks"], status_code=status.HTTP_200_OK)
async def receive_statuspage_webhook(
    request: Request,
    x_statuspage_signature: str | None = Header(default=None),
) -> JSONResponse:
    """
    Endpoint for Statuspage.io webhook POST payloads.

    Statuspage sends one of two payload shapes:
      • incident — triggered when an incident is created / updated / resolved
      • component — triggered when a component status changes

    Both are detected by inspecting the top-level keys in the JSON body.
    """
    body = await request.body()
    _verify_signature(body, x_statuspage_signature)

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON body",
        )

    # Route by payload type
    if "incident" in payload:
        event = IncidentPayload(**payload)
        handle_incident(event)
        return JSONResponse({"received": "incident", "id": event.incident.id})

    if "component" in payload:
        event = ComponentUpdate(**payload)
        handle_component_update(event)
        return JSONResponse({"received": "component", "id": event.component.id})

    logger.warning("Unknown payload shape: keys=%s", list(payload.keys()))
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Unrecognised payload shape",
    )
