"""AI analysis: run inference, read results, export overlays to the PACS."""

from __future__ import annotations

from typing import Annotated, Any

import anyio
import numpy as np
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pydicom.dataset import Dataset
from spashta_ai.errors import ModelUnavailableError, UnsupportedImageError
from spashta_ai.explain.overlay import blend_on_image, render_overlay_rgba
from spashta_ai.explain.secondary_capture import build_secondary_capture, to_part10_bytes
from spashta_ai.preprocessing import to_display_array
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    client_ip,
    get_audit,
    get_inference,
    get_pacs,
    get_session,
    get_settings,
    parse_dicom,
    read_upload,
)
from app.core.config import Settings
from app.core.security import Principal, get_principal
from app.db.models import AiResult
from app.services import results as results_service
from app.services.audit import AuditAction, AuditOutcome, AuditService
from app.services.dicomweb import DicomWebClient, PacsError
from app.services.inference import InferenceService

router = APIRouter(prefix="/ai", tags=["ai"])

_UID_PATTERN = r"^[0-9]+(\.[0-9]+)*$"
Uid = Annotated[str, Field(pattern=_UID_PATTERN, max_length=64)]


class AnalyzeRequest(BaseModel):
    study_instance_uid: Uid
    series_instance_uid: Uid
    sop_instance_uid: Uid
    explain: bool = True


class ExportRequest(BaseModel):
    finding_index: int = Field(ge=0)


class ExportOut(BaseModel):
    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str


@router.get("/status")
async def engine_status(
    inference: InferenceService = Depends(get_inference),
    _: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Which model pipelines are installed and loaded."""
    return inference.status()


async def _run_analysis(
    *,
    ds: Dataset,
    source: str,
    explain: bool,
    request: Request,
    settings: Settings,
    principal: Principal,
    inference: InferenceService,
    audit: AuditService,
    session: AsyncSession,
) -> dict[str, Any]:
    study_uid = str(ds.get("StudyInstanceUID", "") or "") or None
    patient_id = str(ds.get("PatientID", "") or "") or None

    async def record(outcome: AuditOutcome, detail: dict[str, Any]) -> None:
        await audit.record(
            actor=principal.subject,
            action=AuditAction.AI_ANALYZE,
            outcome=outcome,
            resource_type="Study",
            resource_id=study_uid,
            patient_ref=patient_id,
            source_ip=client_ip(request),
            detail={"source": source, **detail},
        )

    try:
        analysis, deid_report = await inference.analyze(ds, explain=explain)
    except ModelUnavailableError as exc:
        await record(AuditOutcome.FAILURE, {"error": "model_unavailable"})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except UnsupportedImageError as exc:
        await record(AuditOutcome.FAILURE, {"error": "unsupported_image"})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    row = await results_service.save_result(
        session,
        analysis=analysis,
        deid=deid_report,
        heatmap_root=settings.heatmap_dir,
        created_by=principal.subject,
        source=source,
        study_uid=study_uid,
        series_uid=str(ds.get("SeriesInstanceUID", "") or "") or None,
        sop_uid=str(ds.get("SOPInstanceUID", "") or "") or None,
        patient_id=patient_id,
    )
    await record(
        AuditOutcome.SUCCESS,
        {"result_id": row.id, "pipeline": row.pipeline, "model": row.model_name},
    )
    return results_service.to_api(row)


@router.post("/analyze", status_code=status.HTTP_201_CREATED)
async def analyze_pacs_instance(
    body: AnalyzeRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    principal: Principal = Depends(get_principal),
    pacs: DicomWebClient = Depends(get_pacs),
    inference: InferenceService = Depends(get_inference),
    audit: AuditService = Depends(get_audit),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Analyse an instance held in the PACS (retrieved over WADO-RS)."""
    try:
        data = await pacs.retrieve_instance(
            body.study_instance_uid, body.series_instance_uid, body.sop_instance_uid
        )
    except PacsError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.status_code == 404 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, str(exc)) from exc
    return await _run_analysis(
        ds=parse_dicom(data),
        source="pacs",
        explain=body.explain,
        request=request,
        settings=settings,
        principal=principal,
        inference=inference,
        audit=audit,
        session=session,
    )


@router.post("/analyze-upload", status_code=status.HTTP_201_CREATED)
async def analyze_uploaded_file(
    request: Request,
    file: UploadFile = File(...),
    explain: bool = Form(True),
    settings: Settings = Depends(get_settings),
    principal: Principal = Depends(get_principal),
    inference: InferenceService = Depends(get_inference),
    audit: AuditService = Depends(get_audit),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Analyse a DICOM file directly, without storing it in the PACS."""
    ds = parse_dicom(await read_upload(file, settings.max_upload_bytes))
    return await _run_analysis(
        ds=ds,
        source="upload",
        explain=explain,
        request=request,
        settings=settings,
        principal=principal,
        inference=inference,
        audit=audit,
        session=session,
    )


@router.get("/results")
async def list_results(
    study_instance_uid: str = Query(pattern=_UID_PATTERN, max_length=64),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    """All AI results for a study, newest first."""
    rows = await session.scalars(
        select(AiResult)
        .where(AiResult.study_instance_uid == study_instance_uid)
        .order_by(AiResult.created_at.desc())
    )
    return [results_service.to_api(row) for row in rows]


async def _get_result(session: AsyncSession, result_id: str) -> AiResult:
    row = await session.get(AiResult, result_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI result not found")
    return row


@router.get("/results/{result_id}")
async def get_result(
    result_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    audit: AuditService = Depends(get_audit),
) -> dict[str, Any]:
    row = await _get_result(session, result_id)
    await audit.record(
        actor=principal.subject,
        action=AuditAction.AI_RESULT_READ,
        resource_type="AiResult",
        resource_id=row.id,
        patient_ref=row.patient_id,
        source_ip=client_ip(request),
    )
    return results_service.to_api(row)


@router.get("/results/{result_id}/heatmaps/{index}", response_class=FileResponse)
async def get_heatmap(
    result_id: str,
    index: int,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(get_principal),
) -> FileResponse:
    """Grad-CAM overlay as a semi-transparent PNG spanning the whole image."""
    row = await _get_result(session, result_id)
    path = results_service.result_dir(settings.heatmap_dir, row.id) / f"{index}.png"
    if index < 0 or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No heatmap for this finding")
    # Private: overlays are derived from patient images.
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.post("/results/{result_id}/export", response_model=ExportOut)
async def export_to_pacs(
    result_id: str,
    body: ExportRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    pacs: DicomWebClient = Depends(get_pacs),
    audit: AuditService = Depends(get_audit),
) -> ExportOut:
    """Store a finding's heatmap in the PACS as a DICOM Secondary Capture (STOW-RS).

    The capture is added to the source study as a new series.
    """
    row = await _get_result(session, result_id)
    if row.source != "pacs" or not (
        row.study_instance_uid and row.series_instance_uid and row.sop_instance_uid
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only results for PACS-stored instances can be exported"
        )
    cam_path = (
        results_service.result_dir(settings.heatmap_dir, row.id) / f"{body.finding_index}.npy"
    )
    findings = row.payload["findings"]
    if body.finding_index >= len(findings) or not cam_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No heatmap for this finding")
    finding = findings[body.finding_index]

    try:
        source_bytes = await pacs.retrieve_instance(
            row.study_instance_uid, row.series_instance_uid, row.sop_instance_uid
        )
    except PacsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    source = parse_dicom(source_bytes)

    def render() -> tuple[bytes, Dataset]:
        image = to_display_array(source)
        overlay = render_overlay_rgba(
            np.load(cam_path),
            image.shape,
            tuple(row.payload["heatmap_roi"]),
            max_side=min(2048, max(image.shape)),
        )
        capture = build_secondary_capture(
            source,
            blend_on_image(image, overlay),
            finding_label=finding["label"],
            model_name=row.model_name,
            model_version=row.model_version,
        )
        return to_part10_bytes(capture), capture

    capture_bytes, capture = await anyio.to_thread.run_sync(render)

    try:
        await pacs.store_instances([capture_bytes])
    except PacsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    await audit.record(
        actor=principal.subject,
        action=AuditAction.AI_EXPORT_TO_PACS,
        resource_type="Study",
        resource_id=row.study_instance_uid,
        patient_ref=row.patient_id,
        source_ip=client_ip(request),
        detail={
            "result_id": row.id,
            "finding": finding["label"],
            "sop_instance_uid": str(capture.SOPInstanceUID),
        },
    )
    return ExportOut(
        study_instance_uid=str(capture.StudyInstanceUID),
        series_instance_uid=str(capture.SeriesInstanceUID),
        sop_instance_uid=str(capture.SOPInstanceUID),
    )
