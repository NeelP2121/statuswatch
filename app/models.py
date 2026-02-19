"""
Pydantic v2 models for Statuspage.io webhook payload shapes.

Reference: https://support.atlassian.com/statuspage/docs/enable-webhook-notifications/
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------
class AffectedComponent(BaseModel):
    id: str
    name: str
    old_status: Optional[str] = None
    new_status: Optional[str] = None


# ---------------------------------------------------------------------------
# Incident payloads
# ---------------------------------------------------------------------------
class IncidentUpdate(BaseModel):
    id: str
    status: str
    body: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    affected_components: list[AffectedComponent] = Field(default_factory=list)


class Incident(BaseModel):
    id: str
    name: str
    status: str                          # investigating / identified / monitoring / resolved
    impact: str                          # none / minor / major / critical
    created_at: datetime
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    shortlink: Optional[str] = None
    incident_updates: list[IncidentUpdate] = Field(default_factory=list)
    components: list[AffectedComponent] = Field(default_factory=list)


class IncidentPayload(BaseModel):
    """Top-level webhook body when an incident event fires."""
    meta: Optional[dict] = None
    incident: Incident


# ---------------------------------------------------------------------------
# Component payloads
# ---------------------------------------------------------------------------
class Component(BaseModel):
    id: str
    name: str
    status: str                          # operational / degraded_performance / partial_outage / major_outage
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    description: Optional[str] = None


class ComponentUpdate(BaseModel):
    """Top-level webhook body when a component status changes."""
    meta: Optional[dict] = None
    component_update: Optional[dict] = None   # raw diff object (old/new status)
    component: Component
