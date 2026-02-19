"""
Event handlers — pure functions that receive validated models and emit
structured log lines to stdout/stderr.

Keeping handlers separate from the HTTP layer makes them trivially testable
and easy to swap for Slack / PagerDuty / email notifiers later.
"""

from __future__ import annotations

import logging

from app.models import ComponentUpdate, IncidentPayload

logger = logging.getLogger("statuswatch.handlers")

# Human-readable severity decorations
IMPACT_EMOJI = {
    "critical": "🔴",
    "major": "🟠",
    "minor": "🟡",
    "none": "🟢",
}

COMPONENT_EMOJI = {
    "major_outage": "🔴",
    "partial_outage": "🟠",
    "degraded_performance": "🟡",
    "operational": "🟢",
    "under_maintenance": "🔵",
}

STATUS_UPPER = {
    "investigating": "INVESTIGATING",
    "identified": "IDENTIFIED",
    "monitoring": "MONITORING",
    "resolved": "✅ RESOLVED",
    "postmortem": "POSTMORTEM",
}


def handle_incident(payload: IncidentPayload) -> None:
    """Log a structured summary of an incident webhook event."""
    inc = payload.incident
    icon = IMPACT_EMOJI.get(inc.impact, "⚪")
    status_label = STATUS_UPPER.get(inc.status, inc.status.upper())

    separator = "=" * 64
    logger.info(separator)
    logger.info("%s  INCIDENT  [%s]  %s", icon, status_label, inc.name)
    logger.info("  ID       : %s", inc.id)
    logger.info("  Impact   : %s", inc.impact)
    logger.info("  Created  : %s", inc.created_at.isoformat())

    if inc.updated_at:
        logger.info("  Updated  : %s", inc.updated_at.isoformat())
    if inc.resolved_at:
        logger.info("  Resolved : %s", inc.resolved_at.isoformat())
    if inc.shortlink:
        logger.info("  Link     : %s", inc.shortlink)

    if inc.components:
        names = ", ".join(c.name for c in inc.components)
        logger.info("  Affected : %s", names)

    # Latest update body
    if inc.incident_updates:
        latest = inc.incident_updates[0]
        logger.info("  Update   : %s", latest.body)

    logger.info(separator)


def handle_component_update(payload: ComponentUpdate) -> None:
    """Log a structured summary of a component status-change event."""
    comp = payload.component
    icon = COMPONENT_EMOJI.get(comp.status, "⚪")

    old_status = "unknown"
    new_status = comp.status
    if payload.component_update:
        old_status = payload.component_update.get("old_status", "unknown")
        new_status = payload.component_update.get("new_status", comp.status)

    old_icon = COMPONENT_EMOJI.get(old_status, "⚪")
    new_icon = COMPONENT_EMOJI.get(new_status, "⚪")

    separator = "-" * 64
    logger.info(separator)
    logger.info("%s  COMPONENT STATUS CHANGE", icon)
    logger.info("  Name     : %s", comp.name)
    logger.info("  ID       : %s", comp.id)
    logger.info(
        "  Transition: %s %s  →  %s %s",
        old_icon, old_status, new_icon, new_status,
    )
    if comp.updated_at:
        logger.info("  At       : %s", comp.updated_at.isoformat())
    logger.info(separator)


# ---------------------------------------------------------------------------
# Dashboard formatters — produce a flat dict for the SSE / event log
# ---------------------------------------------------------------------------

def format_incident_event(payload: IncidentPayload) -> dict:
    """Flatten an incident into a JSON-serialisable dict for the dashboard."""
    inc = payload.incident
    latest_body = inc.incident_updates[0].body if inc.incident_updates else ""
    affected = ", ".join(c.name for c in inc.components) if inc.components else ""
    return {
        "type": "incident",
        "name": inc.name,
        "status": inc.status,
        "impact": inc.impact,
        "affected": affected,
        "update_body": latest_body,
        "shortlink": inc.shortlink or "",
        "timestamp": (inc.updated_at or inc.created_at).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def format_component_event(payload: ComponentUpdate) -> dict:
    """Flatten a component update into a JSON-serialisable dict for the dashboard."""
    comp = payload.component
    old_s = payload.component_update.get("old_status", "unknown") if payload.component_update else "unknown"
    new_s = payload.component_update.get("new_status", comp.status) if payload.component_update else comp.status
    old_icon = COMPONENT_EMOJI.get(old_s, "⚪")
    new_icon = COMPONENT_EMOJI.get(new_s, "⚪")
    return {
        "type": "component",
        "name": comp.name,
        "status": new_s,
        "new_status": new_s,
        "transition": f"{old_icon} {old_s}  →  {new_icon} {new_s}",
        "timestamp": comp.updated_at.strftime("%Y-%m-%d %H:%M:%S UTC") if comp.updated_at else "—",
    }
