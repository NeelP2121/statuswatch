"""
Production-grade tests for StatusWatch webhook receiver.

Covers:
  • Happy-path incident webhook (all statuses)
  • Happy-path component update webhook
  • HMAC signature verification (valid / missing / tampered)
  • Malformed / unknown payload rejection
  • Handler logic in isolation (no HTTP layer)
  • Health endpoint
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

INCIDENT_PAYLOAD = {
    "incident": {
        "id": "inc_001",
        "name": "API Degraded Performance",
        "status": "investigating",
        "impact": "major",
        "created_at": "2025-11-03T14:30:00Z",
        "updated_at": "2025-11-03T14:32:00Z",
        "resolved_at": None,
        "shortlink": "https://stspg.io/abc123",
        "components": [
            {"id": "comp_a", "name": "Chat Completions API", "old_status": "operational", "new_status": "degraded_performance"}
        ],
        "incident_updates": [
            {
                "id": "upd_001",
                "status": "investigating",
                "body": "We are investigating reports of elevated error rates.",
                "created_at": "2025-11-03T14:32:00Z",
                "affected_components": [],
            }
        ],
    }
}

COMPONENT_PAYLOAD = {
    "component": {
        "id": "comp_b",
        "name": "Embeddings API",
        "status": "partial_outage",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-11-03T15:00:00Z",
    },
    "component_update": {
        "old_status": "operational",
        "new_status": "partial_outage",
    },
}


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def make_client(secret: str = "") -> TestClient:
    """Build a TestClient with an optional webhook secret set in env."""
    with patch.dict(os.environ, {"STATUSPAGE_WEBHOOK_SECRET": secret}):
        # Re-import to pick up env changes
        import importlib
        import app.main as main_mod
        importlib.reload(main_mod)
        return TestClient(main_mod.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
class TestHealth:
    def test_returns_ok(self):
        client = make_client()
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert "time" in r.json()


# ---------------------------------------------------------------------------
# Incident webhook
# ---------------------------------------------------------------------------
class TestIncidentWebhook:
    def test_valid_incident_returns_200(self):
        client = make_client()
        r = client.post("/webhook/statuspage", json=INCIDENT_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["received"] == "incident"
        assert r.json()["id"] == "inc_001"

    @pytest.mark.parametrize("status_val", ["investigating", "identified", "monitoring", "resolved"])
    def test_all_incident_statuses_accepted(self, status_val):
        client = make_client()
        payload = json.loads(json.dumps(INCIDENT_PAYLOAD))
        payload["incident"]["status"] = status_val
        r = client.post("/webhook/statuspage", json=payload)
        assert r.status_code == 200

    @pytest.mark.parametrize("impact", ["critical", "major", "minor", "none"])
    def test_all_impact_levels_accepted(self, impact):
        client = make_client()
        payload = json.loads(json.dumps(INCIDENT_PAYLOAD))
        payload["incident"]["impact"] = impact
        r = client.post("/webhook/statuspage", json=payload)
        assert r.status_code == 200

    def test_incident_with_no_updates_accepted(self):
        client = make_client()
        payload = json.loads(json.dumps(INCIDENT_PAYLOAD))
        payload["incident"]["incident_updates"] = []
        r = client.post("/webhook/statuspage", json=payload)
        assert r.status_code == 200

    def test_resolved_incident_accepted(self):
        client = make_client()
        payload = json.loads(json.dumps(INCIDENT_PAYLOAD))
        payload["incident"]["status"] = "resolved"
        payload["incident"]["resolved_at"] = "2025-11-03T16:00:00Z"
        r = client.post("/webhook/statuspage", json=payload)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Component webhook
# ---------------------------------------------------------------------------
class TestComponentWebhook:
    def test_valid_component_returns_200(self):
        client = make_client()
        r = client.post("/webhook/statuspage", json=COMPONENT_PAYLOAD)
        assert r.status_code == 200
        assert r.json()["received"] == "component"
        assert r.json()["id"] == "comp_b"

    @pytest.mark.parametrize("new_status", [
        "operational", "degraded_performance", "partial_outage", "major_outage", "under_maintenance"
    ])
    def test_all_component_statuses_accepted(self, new_status):
        client = make_client()
        payload = json.loads(json.dumps(COMPONENT_PAYLOAD))
        payload["component"]["status"] = new_status
        r = client.post("/webhook/statuspage", json=payload)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------
class TestSignatureVerification:
    SECRET = "super-secret-token"

    def test_valid_signature_accepted(self):
        client = make_client(self.SECRET)
        body = json.dumps(INCIDENT_PAYLOAD).encode()
        sig = _sign(body, self.SECRET)
        r = client.post(
            "/webhook/statuspage",
            content=body,
            headers={"Content-Type": "application/json", "X-Statuspage-Signature": sig},
        )
        assert r.status_code == 200

    def test_missing_signature_rejected(self):
        client = make_client(self.SECRET)
        r = client.post("/webhook/statuspage", json=INCIDENT_PAYLOAD)
        assert r.status_code == 401

    def test_tampered_signature_rejected(self):
        client = make_client(self.SECRET)
        body = json.dumps(INCIDENT_PAYLOAD).encode()
        r = client.post(
            "/webhook/statuspage",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Statuspage-Signature": "sha256=deadbeefdeadbeef",
            },
        )
        assert r.status_code == 401

    def test_no_secret_set_skips_verification(self):
        """When STATUSPAGE_WEBHOOK_SECRET is empty, any request passes."""
        client = make_client("")
        r = client.post("/webhook/statuspage", json=INCIDENT_PAYLOAD)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Malformed / unknown payloads
# ---------------------------------------------------------------------------
class TestMalformedPayloads:
    def test_empty_body_rejected(self):
        client = make_client()
        r = client.post(
            "/webhook/statuspage",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 422

    def test_unknown_shape_rejected(self):
        client = make_client()
        r = client.post("/webhook/statuspage", json={"some_random_key": "value"})
        assert r.status_code == 422

    def test_missing_required_incident_field_rejected(self):
        client = make_client()
        bad = {"incident": {"id": "x"}}   # missing name, status, impact, created_at
        r = client.post("/webhook/statuspage", json=bad)
        assert r.status_code == 422

    def test_non_json_body_rejected(self):
        client = make_client()
        r = client.post(
            "/webhook/statuspage",
            content=b"not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Handler unit tests (no HTTP layer)
# ---------------------------------------------------------------------------
class TestHandlers:
    def test_handle_incident_logs_correctly(self, caplog):
        import logging
        from app.handlers import handle_incident
        from app.models import IncidentPayload

        payload = IncidentPayload(**INCIDENT_PAYLOAD)
        with caplog.at_level(logging.INFO, logger="statuswatch.handlers"):
            handle_incident(payload)

        log_text = "\n".join(caplog.messages)
        assert "INCIDENT" in log_text
        assert "inc_001" in log_text
        assert "API Degraded Performance" in log_text
        assert "Chat Completions API" in log_text

    def test_handle_component_update_logs_correctly(self, caplog):
        import logging
        from app.handlers import handle_component_update
        from app.models import ComponentUpdate

        payload = ComponentUpdate(**COMPONENT_PAYLOAD)
        with caplog.at_level(logging.INFO, logger="statuswatch.handlers"):
            handle_component_update(payload)

        log_text = "\n".join(caplog.messages)
        assert "COMPONENT STATUS CHANGE" in log_text
        assert "Embeddings API" in log_text
        assert "operational" in log_text
        assert "partial_outage" in log_text

    def test_handle_incident_resolved_logs_resolved_at(self, caplog):
        import logging
        from app.handlers import handle_incident
        from app.models import IncidentPayload

        payload_dict = json.loads(json.dumps(INCIDENT_PAYLOAD))
        payload_dict["incident"]["status"] = "resolved"
        payload_dict["incident"]["resolved_at"] = "2025-11-03T16:00:00Z"
        payload = IncidentPayload(**payload_dict)

        with caplog.at_level(logging.INFO, logger="statuswatch.handlers"):
            handle_incident(payload)

        assert any("Resolved" in m for m in caplog.messages)
