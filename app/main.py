"""
StatusWatch — FastAPI Webhook Receiver + Live Dashboard
Receives push notifications from Statuspage.io-powered status pages and
streams them to a live browser dashboard via Server-Sent Events (SSE).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from app.models import ComponentUpdate, IncidentPayload
from app.handlers import handle_incident, handle_component_update, format_incident_event, format_component_event

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
# In-memory event store (last 50 events) + SSE subscriber queues
# ---------------------------------------------------------------------------
MAX_EVENTS = 50
event_log: deque[dict] = deque(maxlen=MAX_EVENTS)
sse_subscribers: list[asyncio.Queue] = []

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="StatusWatch",
    description="Event-driven Statuspage.io webhook receiver",
    version="1.0.0",
)

WEBHOOK_SECRET = os.getenv("STATUSPAGE_WEBHOOK_SECRET", "")


def _verify_signature(body: bytes, signature_header: str | None) -> None:
    if not WEBHOOK_SECRET:
        return
    if not signature_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Missing X-Statuspage-Signature header")
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"sha256={expected}", signature_header):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid webhook signature")


def _broadcast(event: dict) -> None:
    """Store event and push to all connected SSE clients."""
    event_log.appendleft(event)
    dead = []
    for q in sse_subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        sse_subscribers.remove(q)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>StatusWatch 🚦</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:#0f1117;color:#e2e8f0;min-height:100vh;padding:2rem}
    header{max-width:900px;margin:0 auto 2rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:gap}
    h1{font-size:1.8rem;font-weight:700;
       background:linear-gradient(135deg,#818cf8,#c084fc);
       -webkit-background-clip:text;-webkit-text-fill-color:transparent}
    .badge{display:inline-flex;align-items:center;gap:6px;
           background:#22c55e22;color:#22c55e;border:1px solid #22c55e44;
           border-radius:999px;padding:4px 12px;font-size:0.78rem;font-weight:600}
    .dot{width:7px;height:7px;border-radius:50%;background:#22c55e;animation:pulse 2s infinite}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
    .container{max-width:900px;margin:0 auto}
    .toolbar{display:flex;gap:.6rem;margin-bottom:1.2rem;flex-wrap:wrap}
    .filter-btn{padding:.35rem .9rem;border-radius:6px;border:1px solid #2d3148;
                background:#1a1d27;color:#94a3b8;font-size:.8rem;cursor:pointer;transition:.15s}
    .filter-btn.active,.filter-btn:hover{background:#818cf8;color:#fff;border-color:#818cf8}
    #feed{display:flex;flex-direction:column;gap:.75rem}
    .event-card{background:#1a1d27;border:1px solid #2d3148;border-radius:12px;
                padding:1rem 1.2rem;animation:slideIn .3s ease}
    @keyframes slideIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
    .event-card.critical{border-left:3px solid #ef4444}
    .event-card.major   {border-left:3px solid #f97316}
    .event-card.minor   {border-left:3px solid #eab308}
    .event-card.none,.event-card.operational{border-left:3px solid #22c55e}
    .event-card.component{border-left:3px solid #818cf8}
    .card-header{display:flex;align-items:center;gap:.6rem;margin-bottom:.4rem}
    .pill{font-size:.68rem;font-weight:700;padding:2px 8px;border-radius:4px;letter-spacing:.05em}
    .pill-incident{background:#f9731622;color:#fb923c;border:1px solid #f9731644}
    .pill-component{background:#818cf822;color:#a78bfa;border:1px solid #818cf844}
    .pill-resolved{background:#22c55e22;color:#22c55e;border:1px solid #22c55e44}
    .pill-investigating{background:#ef444422;color:#f87171;border:1px solid #ef444444}
    .pill-monitoring{background:#eab30822;color:#fbbf24;border:1px solid #eab30844}
    .card-title{font-weight:600;font-size:.95rem}
    .card-body{font-size:.85rem;color:#94a3b8;margin:.3rem 0}
    .card-meta{font-size:.75rem;color:#475569;display:flex;gap:1rem;margin-top:.4rem;flex-wrap:wrap}
    .card-meta span{display:flex;align-items:center;gap:4px}
    .empty{text-align:center;color:#475569;padding:4rem 0;font-size:.9rem}
    .empty-icon{font-size:2.5rem;display:block;margin-bottom:.5rem}
    a.docs-link{color:#818cf8;font-size:.85rem;text-decoration:none}
    a.docs-link:hover{text-decoration:underline}
  </style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center;gap:1rem">
    <h1>StatusWatch 🚦</h1>
    <div class="badge" id="conn-badge"><span class="dot"></span> CONNECTING...</div>
  </div>
  <a class="docs-link" href="/docs">API Docs →</a>
</header>

<div class="container">
  <div class="toolbar">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="incident">Incidents</button>
    <button class="filter-btn" data-filter="component">Components</button>
    <button class="filter-btn" data-filter="resolved">Resolved</button>
  </div>
  <div id="feed">
    <div class="empty" id="empty-state">
      <span class="empty-icon">📡</span>
      Listening for status events...<br/>
      <small style="color:#374151;margin-top:.3rem;display:block">
        Events will appear here in real time when Statuspage webhooks arrive.
        <br>Send a test via <a class="docs-link" href="/docs#/webhooks/receive_statuspage_webhook_webhook_statuspage_post">/docs</a> or curl.
      </small>
    </div>
  </div>
</div>

<script>
  const feed = document.getElementById('feed');
  const emptyState = document.getElementById('empty-state');
  const badge = document.getElementById('conn-badge');
  let activeFilter = 'all';
  let allCards = [];

  const IMPACT_ICON = {critical:'🔴',major:'🟠',minor:'🟡',none:'🟢',operational:'🟢',
    degraded_performance:'🟡',partial_outage:'🟠',major_outage:'🔴',under_maintenance:'🔵'};

  function statusPill(s) {
    const cls = s === 'resolved' ? 'pill-resolved'
              : s === 'investigating' ? 'pill-investigating'
              : s === 'monitoring' ? 'pill-monitoring'
              : 'pill-incident';
    return `<span class="pill ${cls}">${s.toUpperCase()}</span>`;
  }

  function renderCard(ev) {
    const card = document.createElement('div');
    const impact = ev.impact || ev.new_status || 'none';
    const type   = ev.type;
    card.className = `event-card ${type === 'component' ? 'component' : impact}`;
    card.dataset.type = type;
    card.dataset.resolved = ev.status === 'resolved' ? 'true' : 'false';

    const icon = IMPACT_ICON[impact] || '⚪';
    const pillClass = type === 'component' ? 'pill-component' : 'pill-incident';
    const typeLabel = type === 'component' ? 'COMPONENT' : 'INCIDENT';

    let bodyHtml = '';
    if (ev.update_body) bodyHtml = `<div class="card-body">${ev.update_body}</div>`;
    if (ev.transition)  bodyHtml = `<div class="card-body">${ev.transition}</div>`;

    card.innerHTML = `
      <div class="card-header">
        <span>${icon}</span>
        <span class="pill ${pillClass}">${typeLabel}</span>
        ${ev.status ? statusPill(ev.status) : ''}
        <span class="card-title">${ev.name}</span>
      </div>
      ${bodyHtml}
      <div class="card-meta">
        <span>🕐 ${ev.timestamp}</span>
        ${ev.affected ? `<span>📦 ${ev.affected}</span>` : ''}
        ${ev.shortlink ? `<span><a class="docs-link" href="${ev.shortlink}" target="_blank">Details →</a></span>` : ''}
      </div>`;
    return card;
  }

  function applyFilter() {
    allCards.forEach(({card, type, resolved}) => {
      const show = activeFilter === 'all'
                || activeFilter === type
                || (activeFilter === 'resolved' && resolved);
      card.style.display = show ? '' : 'none';
    });
    emptyState.style.display = allCards.filter(c =>
      c.card.style.display !== 'none').length === 0 ? '' : 'none';
  }

  function addEvent(ev) {
    emptyState.style.display = 'none';
    const card = renderCard(ev);
    feed.insertBefore(card, feed.firstChild);
    allCards.unshift({card, type: ev.type, resolved: ev.status === 'resolved'});
    applyFilter();
  }

  // Load existing events on page load
  fetch('/events').then(r => r.json()).then(events => {
    events.forEach(addEvent);
  });

  // Filter buttons
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeFilter = btn.dataset.filter;
      applyFilter();
    });
  });

  // SSE — real-time updates
  function connect() {
    const es = new EventSource('/stream');
    es.onopen = () => {
      badge.innerHTML = '<span class="dot"></span> LIVE';
    };
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      addEvent(ev);
    };
    es.onerror = () => {
      badge.innerHTML = '<span class="dot" style="background:#ef4444;animation:none"></span> RECONNECTING...';
      es.close();
      setTimeout(connect, 3000);
    };
  }
  connect();
</script>
</body>
</html>"""


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/events", tags=["ops"])
async def get_events() -> list[dict]:
    """Return all stored events (newest first) for initial page load."""
    return list(event_log)


@app.get("/stream", tags=["ops"])
async def sse_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events stream — pushes new events to connected browsers."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    sse_subscribers.append(queue)

    async def generator():
        try:
            yield "retry: 3000\n\n"  # tell browser to reconnect after 3s on disconnect
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # prevent proxy timeouts
        finally:
            if queue in sse_subscribers:
                sse_subscribers.remove(queue)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/webhook/statuspage", tags=["webhooks"], status_code=status.HTTP_200_OK)
async def receive_statuspage_webhook(
    request: Request,
    x_statuspage_signature: str | None = Header(default=None),
) -> JSONResponse:
    body = await request.body()
    _verify_signature(body, x_statuspage_signature)

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Invalid JSON body")

    if "incident" in payload:
        event = IncidentPayload(**payload)
        handle_incident(event)
        dashboard_event = format_incident_event(event)
        _broadcast(dashboard_event)
        return JSONResponse({"received": "incident", "id": event.incident.id})

    if "component" in payload:
        event = ComponentUpdate(**payload)
        handle_component_update(event)
        dashboard_event = format_component_event(event)
        _broadcast(dashboard_event)
        return JSONResponse({"received": "component", "id": event.component.id})

    logger.warning("Unknown payload shape: keys=%s", list(payload.keys()))
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Unrecognised payload shape")
