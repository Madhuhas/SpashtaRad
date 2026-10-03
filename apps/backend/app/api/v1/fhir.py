"""SMART on FHIR R4: EHR launch, OAuth callback and patient context."""

from __future__ import annotations

import datetime as dt
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    client_ip,
    get_audit,
    get_external_http,
    get_pacs,
    get_session,
    get_settings,
)
from app.core.config import Settings
from app.db.models import SmartSession
from app.services import smart
from app.services.audit import AuditAction, AuditOutcome, AuditService
from app.services.dicomweb import DicomWebClient, PacsError

router = APIRouter(prefix="/fhir", tags=["fhir"])

SESSION_COOKIE = "spashtarad_smart_session"
_LAUNCH_TTL = dt.timedelta(minutes=10)
_MAX_IDENTIFIERS_QUERIED = 3


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _first(study: dict[str, Any], tag: str) -> Any:
    """First value of a tag in a DICOM JSON (PS3.18 Annex F) object."""
    values = study.get(tag, {}).get("Value") or [None]
    return values[0]


@router.get("/launch")
async def launch(
    request: Request,
    iss: str = Query(description="FHIR base URL of the launching EHR"),
    launch: str | None = Query(None, description="Opaque EHR launch context (absent = standalone)"),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_external_http),
    session: AsyncSession = Depends(get_session),
    audit: AuditService = Depends(get_audit),
) -> RedirectResponse:
    """Start a SMART launch: discover the EHR's OAuth endpoints and redirect to authorise."""
    if not smart.is_allowed_issuer(iss, settings.smart_allowed_issuers):
        await audit.record(
            actor="anonymous",
            action=AuditAction.FHIR_LAUNCH,
            outcome=AuditOutcome.FAILURE,
            source_ip=client_ip(request),
            detail={"error": "issuer_not_allowed", "iss": iss[:512]},
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "FHIR server is not on the allow-list")

    try:
        endpoints = await smart.discover(http, iss)
    except smart.SmartError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    verifier, challenge = smart.new_pkce_pair()
    record = SmartSession(
        id=secrets.token_urlsafe(32),
        created_at=_now().isoformat(),
        state=secrets.token_urlsafe(24),
        issuer=smart.normalise_issuer(iss),
        token_endpoint=endpoints.token_endpoint,
        code_verifier=verifier,
    )
    session.add(record)
    await session.commit()

    # EHR launch carries context via `launch`; standalone asks the EHR to pick a patient.
    scope = f"{'launch' if launch else 'launch/patient'} {settings.smart_scopes}"
    url = smart.build_authorize_url(
        endpoints,
        client_id=settings.smart_client_id,
        redirect_uri=settings.smart_redirect_uri,
        scope=scope,
        state=record.state,
        issuer=iss,
        code_challenge=challenge,
        launch=launch,
    )
    await audit.record(
        actor="anonymous",
        action=AuditAction.FHIR_LAUNCH,
        source_ip=client_ip(request),
        detail={"iss": record.issuer, "ehr_launch": launch is not None},
    )
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/callback")
async def callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_external_http),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """OAuth redirect target: exchange the code and establish the viewer session."""
    record = await session.scalar(select(SmartSession).where(SmartSession.state == state))
    if record is None or record.access_token is not None:
        # Unknown state, or a state that was already redeemed (replay).
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown or already used launch state")
    if _now() - dt.datetime.fromisoformat(record.created_at) > _LAUNCH_TTL:
        await session.delete(record)
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Launch expired; start again from the EHR")
    if error or not code:
        await session.delete(record)
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Authorisation was denied by the EHR")

    try:
        token = await smart.exchange_code(
            http,
            record.token_endpoint,
            code=code,
            code_verifier=record.code_verifier,
            client_id=settings.smart_client_id,
            redirect_uri=settings.smart_redirect_uri,
        )
    except smart.SmartError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    lifetime = int(token.get("expires_in") or 3600)
    record.access_token = token["access_token"]
    record.expires_at = (_now() + dt.timedelta(seconds=lifetime)).isoformat()
    record.patient_fhir_id = token.get("patient")
    record.code_verifier = ""  # single use
    await session.commit()

    response = RedirectResponse(
        f"{settings.frontend_url.rstrip('/')}/?smart=1", status_code=status.HTTP_302_FOUND
    )
    # NOTE: when the viewer is embedded in an EHR iframe on another site, the
    # cookie must be SameSite=None; Secure (HTTPS only) to be sent.
    response.set_cookie(
        SESSION_COOKIE,
        record.id,
        max_age=lifetime,
        httponly=True,
        secure=settings.frontend_url.startswith("https://"),
        samesite="lax",
        path="/",
    )
    return response


async def _active_session(session: AsyncSession, session_id: str | None) -> SmartSession:
    record = await session.get(SmartSession, session_id) if session_id else None
    if (
        record is None
        or record.access_token is None
        or record.expires_at is None
        or dt.datetime.fromisoformat(record.expires_at) <= _now()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No active SMART session")
    return record


@router.get("/context")
async def patient_context(
    request: Request,
    session_id: str | None = Cookie(None, alias=SESSION_COOKIE),
    http: httpx.AsyncClient = Depends(get_external_http),
    pacs: DicomWebClient = Depends(get_pacs),
    session: AsyncSession = Depends(get_session),
    audit: AuditService = Depends(get_audit),
) -> dict[str, Any]:
    """Patient demographics and encounters from the EHR, matched to PACS studies."""
    record = await _active_session(session, session_id)
    if not record.patient_fhir_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "The EHR launch did not include a patient")

    try:
        patient = smart.summarise_patient(
            await smart.fetch_fhir(
                http, record.issuer, f"Patient/{record.patient_fhir_id}", record.access_token
            )
        )
    except smart.SmartError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    try:
        encounters = smart.summarise_encounters(
            await smart.fetch_fhir(
                http,
                record.issuer,
                f"Encounter?patient={record.patient_fhir_id}&_sort=-date&_count=5",
                record.access_token,
            )
        )
    except smart.SmartError:
        encounters = []  # optional context: the scope may not have been granted

    # Link EHR -> PACS on the medical record number (DICOM PatientID).
    candidates = [patient["mrn"]] if patient["mrn"] else []
    candidates += [i["value"] for i in patient["identifiers"] if i["value"] not in candidates]
    studies: list[dict[str, Any]] = []
    pacs_available = True
    for identifier in candidates[:_MAX_IDENTIFIERS_QUERIED]:
        try:
            matches = await pacs.search_studies(
                {"PatientID": identifier, "includefield": "00081030,00080061"}
            )
        except PacsError:
            pacs_available = False
            break
        studies += [
            {
                "study_instance_uid": _first(study, "0020000D"),
                "study_date": _first(study, "00080020"),
                "description": _first(study, "00081030"),
                "accession_number": _first(study, "00080050"),
                "modalities": study.get("00080061", {}).get("Value", []),
                "matched_identifier": identifier,
            }
            for study in matches
        ]
        if studies:
            break

    await audit.record(
        actor=f"smart:{record.issuer}",
        action=AuditAction.FHIR_PATIENT_READ,
        resource_type="Patient",
        resource_id=record.patient_fhir_id,
        patient_ref=patient["mrn"],
        source_ip=client_ip(request),
        detail={"studies_matched": len(studies)},
    )
    return {
        "issuer": record.issuer,
        "patient": patient,
        "encounters": encounters,
        "studies": studies,
        "pacs_available": pacs_available,
    }


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def end_session(
    session_id: str | None = Cookie(None, alias=SESSION_COOKIE),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Discard the SMART session and its stored access token."""
    record = await session.get(SmartSession, session_id) if session_id else None
    if record is not None:
        await session.delete(record)
        await session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
