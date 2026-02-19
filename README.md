# StatusWatch 🚦

An event-driven, Dockerized FastAPI webhook receiver for
[Statuspage.io](https://www.atlassian.com/software/statuspage)-powered
status pages (OpenAI, GitHub, Stripe, etc.).

Instead of polling an Atom feed, this service **receives push notifications**
directly from Statuspage — fully event-driven, zero polling, zero wasted bandwidth.

---

## Architecture

```
Statuspage.io ──POST──▶ /webhook/statuspage ──▶ handle_incident()
                                             └──▶ handle_component_update()
                                                         │
                                                         ▼
                                                  Structured logs
                                             (stdout / Docker log driver)
```

---

## Quick Start

### 1. Clone & configure

```bash
cp .env.example .env          # optional: set STATUSPAGE_WEBHOOK_SECRET
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

The receiver listens on **http://localhost:8000**.

### 3. Register the webhook URL

In your Statuspage dashboard → **Notifications → Webhooks**, add:

```
https://your-domain.com/webhook/statuspage
```

Use [ngrok](https://ngrok.com/) during local development (see `docker-compose.yml`).

---

## Configuration

| Env var | Default | Description |
|---|---|---|
| `STATUSPAGE_WEBHOOK_SECRET` | *(empty)* | HMAC secret from Statuspage webhook settings. If empty, signature verification is skipped. |

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/webhook/statuspage` | Statuspage webhook receiver |
| `GET` | `/docs` | Auto-generated Swagger UI |

---

## Running Tests

```bash
# Install deps
pip install -r requirements.txt

# Run all tests with verbose output
pytest -v

# Run a specific class
pytest tests/test_webhook.py::TestSignatureVerification -v
```

### Test coverage

| Area | Tests |
|---|---|
| Health endpoint | ✅ |
| Incident webhook — all statuses | ✅ |
| Incident webhook — all impact levels | ✅ |
| Component webhook — all statuses | ✅ |
| HMAC verification (valid / missing / tampered / disabled) | ✅ |
| Malformed / unknown payloads | ✅ |
| Handler logic in isolation (no HTTP) | ✅ |

---

## Project Structure

```
statuswatch/
├── app/
│   ├── main.py        # FastAPI app, routes, HMAC verification
│   ├── models.py      # Pydantic v2 payload models
│   └── handlers.py    # Business logic (logging, alerting)
├── tests/
│   └── test_webhook.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pyproject.toml
```

---

## Extending

**Add Slack alerts** — edit `app/handlers.py`:
```python
import httpx
httpx.post(SLACK_WEBHOOK_URL, json={"text": f"🔴 {inc.name}"})
```

**Add more providers** — register additional webhook URLs in each provider's
Statuspage dashboard pointing to the same `/webhook/statuspage` endpoint.
The `meta.page.id` field in the payload identifies which provider fired it.

**Scale horizontally** — the app is stateless; run multiple replicas behind a
load balancer. Each webhook POST is self-contained.
