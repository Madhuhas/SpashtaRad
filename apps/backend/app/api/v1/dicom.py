"""DICOM ingestion and de-identified export."""

from __future__ import annotations

import io
import json

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel

from app.api.deps import (
    client_ip,
    get_audit,
    get_inference,
    get_pacs,
    get_settings,
    parse_dicom,
    read_upload,
)
from app.core.config import Settings
from app.core.security import Principal, get_principal
from app.services.audit import AuditAction, AuditOutcome, AuditService
from app.services.deid import Deidentifier
from app.services.dicomweb import DicomWebClient, PacsError
from app.services.inference import InferenceService

router = APIRouter(prefix="/dicom", tags=["dicom"])


class RoutingOut(BaseModel):
    region: str
    body_part: str | None
    modality: str | None
    source: str
    confidence: float
    notes: list[str]


class UploadOut(BaseModel):
    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str
    routing: RoutingOut


@router.post("/upload", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def upload_dicom(
    request: Request,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    principal: Principal = Depends(get_principal),
    pacs: DicomWebClient = Depends(get_pacs),
    inference: InferenceService = Depends(get_inference),
    audit: AuditService = Depends(get_audit),
) -> UploadOut:
    """Store a DICOM file in the PACS (STOW-RS) and report its anatomy routing."""
    data = await read_upload(file, settings.max_upload_bytes)
    ds = parse_dicom(data)
    for keyword in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"):
        if not ds.get(keyword):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"DICOM object is missing {keyword}")

    async def record(outcome: AuditOutcome, detail: dict | None = None) -> None:
        await audit.record(
            actor=principal.subject,
            action=AuditAction.DICOM_STORE,
            outcome=outcome,
            resource_type="Study",
            resource_id=str(ds.StudyInstanceUID),
            patient_ref=str(ds.get("PatientID", "") or "") or None,
            source_ip=client_ip(request),
            detail={"sop_instance_uid": str(ds.SOPInstanceUID), **(detail or {})},
        )

    try:
        await pacs.store_instances([data])
    except PacsError as exc:
        await record(AuditOutcome.FAILURE, {"error": str(exc)})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    await record(AuditOutcome.SUCCESS)

    routing = inference.route(ds)
    return UploadOut(
        study_instance_uid=str(ds.StudyInstanceUID),
        series_instance_uid=str(ds.SeriesInstanceUID),
        sop_instance_uid=str(ds.SOPInstanceUID),
        routing=RoutingOut(
            region=routing.region.value,
            body_part=routing.body_part,
            modality=routing.modality,
            source=routing.source.value,
            confidence=routing.confidence,
            notes=list(routing.notes),
        ),
    )


@router.post(
    "/deidentify",
    response_class=Response,
    responses={200: {"content": {"application/dicom": {}}}},
)
async def deidentify_dicom(
    request: Request,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    principal: Principal = Depends(get_principal),
    audit: AuditService = Depends(get_audit),
) -> Response:
    """Return a header-de-identified copy of a DICOM file.

    The de-identification report (including pixel-data warnings) is returned
    in the ``X-Deidentification-Report`` response header as JSON.
    """
    ds = parse_dicom(await read_upload(file, settings.max_upload_bytes))
    original_study = str(ds.get("StudyInstanceUID", "") or "") or None
    original_patient = str(ds.get("PatientID", "") or "") or None

    deidentifier = Deidentifier(settings.deid_uid_salt.get_secret_value())
    clean, report = deidentifier.deidentify(ds)
    buffer = io.BytesIO()
    clean.save_as(buffer, enforce_file_format=True)

    await audit.record(
        actor=principal.subject,
        action=AuditAction.DICOM_DEIDENTIFIED_EXPORT,
        resource_type="Study",
        resource_id=original_study,
        patient_ref=original_patient,
        source_ip=client_ip(request),
        detail=report.as_dict(),
    )
    return Response(
        content=buffer.getvalue(),
        media_type="application/dicom",
        headers={
            "Content-Disposition": 'attachment; filename="deidentified.dcm"',
            "X-Deidentification-Report": json.dumps(report.as_dict()),
        },
    )
