"""DICOMweb gateway: the viewer's only route to the PACS.

Cornerstone3D talks QIDO-RS / WADO-RS / STOW-RS to ``/dicomweb/...``; requests
are authenticated, audited and streamed through to the PACS. PACS credentials
never reach the browser.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.api.deps import client_ip, get_audit, get_pacs
from app.core.security import Principal, get_principal
from app.services.audit import AuditAction, AuditService
from app.services.dicomweb import DicomWebClient, PacsError, validate_uid

router = APIRouter(prefix="/dicomweb", tags=["dicomweb"])

# DICOMweb resource roots the gateway will forward. Anything else (including
# the PACS's native admin API) is refused.
_ALLOWED_ROOTS = frozenset({"studies", "series", "instances"})
_COLLECTIONS = frozenset({"studies", "series", "instances"})
_REQUEST_HEADERS = ("accept", "content-type", "accept-encoding")
_RESPONSE_HEADERS = ("content-type", "content-length", "content-encoding", "etag", "last-modified")

# A viewer issues one WADO request per frame; logging each would bury the
# trail. Retrievals are therefore audited once per (actor, study, series)
# within this window.
_RETRIEVE_AUDIT_WINDOW_SECONDS = 300.0


def _classify(method: str, segments: list[str]) -> AuditAction:
    if method == "POST":
        return AuditAction.DICOM_STORE
    # A path ending on a collection name is a QIDO-RS search.
    return AuditAction.DICOM_QUERY if segments[-1] in _COLLECTIONS else AuditAction.DICOM_RETRIEVE


def _should_audit(request: Request, key: tuple[str, ...]) -> bool:
    """De-duplicate retrieval audits within the window (per process)."""
    seen: dict[tuple[str, ...], float] = request.app.state.retrieve_audit_cache
    now = time.monotonic()
    if len(seen) > 10_000:
        for stale in [k for k, t in seen.items() if now - t > _RETRIEVE_AUDIT_WINDOW_SECONDS]:
            del seen[stale]
    last = seen.get(key)
    if last is not None and now - last < _RETRIEVE_AUDIT_WINDOW_SECONDS:
        return False
    seen[key] = now
    return True


@router.api_route("/{path:path}", methods=["GET", "POST"])
async def proxy(
    path: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    pacs: DicomWebClient = Depends(get_pacs),
    audit: AuditService = Depends(get_audit),
) -> StreamingResponse:
    """Forward a DICOMweb request to the PACS and stream the response back."""
    segments = [segment for segment in path.split("/") if segment]
    if not segments or segments[0] not in _ALLOWED_ROOTS or ".." in segments:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a DICOMweb resource")
    if request.method == "POST" and segments[0] != "studies":
        raise HTTPException(status.HTTP_405_METHOD_NOT_ALLOWED, "STOW-RS targets /studies")

    study_uid = series_uid = None
    try:
        if segments[0] == "studies" and len(segments) > 1:
            study_uid = validate_uid(segments[1])
        if len(segments) > 3 and segments[2] == "series":
            series_uid = validate_uid(segments[3])
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    action = _classify(request.method, segments)
    if action is not AuditAction.DICOM_RETRIEVE or _should_audit(
        request, (principal.subject, study_uid or "", series_uid or "")
    ):
        await audit.record(
            actor=principal.subject,
            action=action,
            resource_type="Study" if study_uid else None,
            resource_id=study_uid,
            source_ip=client_ip(request),
            detail={"series_instance_uid": series_uid} if series_uid else None,
        )

    headers = {name: request.headers[name] for name in _REQUEST_HEADERS if name in request.headers}
    try:
        upstream = await pacs.forward(
            request.method,
            "/".join(segments),
            params=request.query_params,
            headers=headers,
            content=request.stream() if request.method == "POST" else None,
        )
    except PacsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers={
            name: upstream.headers[name] for name in _RESPONSE_HEADERS if name in upstream.headers
        },
        background=BackgroundTask(upstream.aclose),
    )
