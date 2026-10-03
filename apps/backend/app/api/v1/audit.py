"""Audit trail access for compliance reporting (admin only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from app.api.deps import client_ip, get_audit
from app.core.security import ROLE_ADMIN, Principal, require_role
from app.services.audit import AuditAction, AuditService

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: str
    actor: str
    action: str
    outcome: str
    resource_type: str | None
    resource_id: str | None
    patient_ref: str | None
    source_ip: str | None
    request_id: str | None
    detail: dict[str, Any] | None
    hash: str


class ChainStatusOut(BaseModel):
    valid: bool
    events_checked: int
    first_invalid_id: int | None


@router.get("/events", response_model=list[AuditEventOut])
async def list_events(
    request: Request,
    actor: str | None = None,
    action: str | None = None,
    resource_id: str | None = None,
    patient_ref: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_role(ROLE_ADMIN)),
    audit: AuditService = Depends(get_audit),
) -> list[Any]:
    """Search audit events, newest first. Reading the log is itself audited."""
    await audit.record(
        actor=principal.subject,
        action=AuditAction.AUDIT_READ,
        source_ip=client_ip(request),
        detail={"filters": {"actor": actor, "action": action, "resource_id": resource_id}},
    )
    return await audit.search(
        actor=actor,
        action=action,
        resource_id=resource_id,
        patient_ref=patient_ref,
        limit=limit,
        offset=offset,
    )


@router.get("/verify", response_model=ChainStatusOut)
async def verify_chain(
    _: Principal = Depends(require_role(ROLE_ADMIN)),
    audit: AuditService = Depends(get_audit),
) -> Any:
    """Recompute the hash chain to detect tampering with historical events."""
    return await audit.verify()
