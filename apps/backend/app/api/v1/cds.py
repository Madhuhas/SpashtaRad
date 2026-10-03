"""CDS Hooks 1.0 services.

Mounted twice (see ``app.main``): at the spec's conventional root
``/cds-services`` for EHR registration, and under ``/api/v1/cds`` alongside
the rest of the versioned API.

Security note: the specification has the EHR authenticate each call with a
signed JWT (``Authorization: Bearer``), verified against the EHR's registered
JWKS. That check is not implemented yet, so these endpoints must only be
exposed to trusted networks until it is.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_audit, get_external_http, get_session, get_settings
from app.core.config import Settings
from app.db.models import AiResult
from app.services import cds, smart
from app.services.audit import AuditAction, AuditService

router = APIRouter(tags=["cds-hooks"])

_MAX_RESULTS_CONSIDERED = 20


class FhirAuthorization(BaseModel):
    model_config = ConfigDict(extra="ignore")
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - OAuth token type, not a secret


class HookRequest(BaseModel):
    """CDS Hooks request body (fields this service uses; extras are ignored)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    hook: str
    hook_instance: str = Field(alias="hookInstance")
    context: dict[str, Any]
    fhir_server: str | None = Field(None, alias="fhirServer")
    fhir_authorization: FhirAuthorization | None = Field(None, alias="fhirAuthorization")
    prefetch: dict[str, Any] | None = None


@router.get("/cds-services")
async def discovery() -> dict[str, Any]:
    """CDS Hooks discovery endpoint."""
    return {"services": cds.SERVICES}


@router.post("/cds-services/{service_id}")
async def invoke(
    service_id: str,
    body: HookRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_external_http),
    session: AsyncSession = Depends(get_session),
    audit: AuditService = Depends(get_audit),
) -> dict[str, Any]:
    service = cds.SERVICES_BY_ID.get(service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown CDS service")
    if body.hook != service["hook"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Service '{service_id}' handles '{service['hook']}' only"
        )
    context_patient_id = body.context.get("patientId")
    if not context_patient_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "context.patientId is required")

    patient = (body.prefetch or {}).get("patient")
    if (
        patient is None
        and body.fhir_server
        and body.fhir_authorization
        # Never call a caller-supplied URL that is not an approved EHR.
        and smart.is_allowed_issuer(body.fhir_server, settings.smart_allowed_issuers)
    ):
        try:
            patient = await smart.fetch_fhir(
                http,
                body.fhir_server,
                f"Patient/{context_patient_id}",
                body.fhir_authorization.access_token,
            )
        except smart.SmartError:
            patient = None  # fall back to matching on the FHIR id alone

    identifiers = cds.patient_identifiers(patient, str(context_patient_id))
    rows = list(
        await session.scalars(
            select(AiResult)
            .where(AiResult.patient_id.in_(identifiers))
            .order_by(AiResult.created_at.desc())
            .limit(_MAX_RESULTS_CONSIDERED)
        )
    )
    # Keep only the newest result per study.
    latest: dict[str, AiResult] = {}
    for row in rows:
        latest.setdefault(row.study_instance_uid or row.id, row)

    cards = cds.build_cards(list(latest.values()), hook=body.hook, viewer_url=settings.frontend_url)
    await audit.record(
        actor="cds-client",
        action=AuditAction.CDS_HOOK,
        resource_type="Patient",
        resource_id=str(context_patient_id),
        patient_ref=identifiers[0] if identifiers else None,
        source_ip=client_ip(request),
        detail={"service": service_id, "hook_instance": body.hook_instance, "cards": len(cards)},
    )
    return {"cards": cards}


@router.post("/cds-services/{service_id}/feedback", status_code=status.HTTP_200_OK)
async def feedback(service_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Accept card feedback (accepted / overridden). Currently acknowledged only."""
    if service_id not in cds.SERVICES_BY_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown CDS service")
    return {}
