"""Persistence of AI results: database row + heatmap files."""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from spashta_ai import AnalysisResult
from spashta_ai.explain.overlay import encode_png, render_overlay_rgba
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AiResult
from app.services.deid import DeidReport
from app.services.reporting import build_report, codes_for

API_PREFIX = "/api/v1"


def result_dir(heatmap_root: Path, result_id: str) -> Path:
    # result_id is a server-generated UUID; validate anyway so a crafted id can
    # never escape the heatmap directory.
    return heatmap_root / str(uuid.UUID(result_id))


def build_payload(
    result_id: str, analysis: AnalysisResult, deid: DeidReport, heatmap_root: Path
) -> dict[str, Any]:
    """Convert an engine result to the API payload and write heatmap files.

    For each explained finding two files are written: ``<n>.png`` (RGBA overlay
    for the viewer) and ``<n>.npy`` (raw activation map, used for DICOM export).
    """
    region = analysis.routing.region.value
    directory = result_dir(heatmap_root, result_id)

    findings: list[dict[str, Any]] = []
    for index, finding in enumerate(analysis.findings):
        heatmap_url = None
        if finding.heatmap is not None:
            directory.mkdir(parents=True, exist_ok=True)
            overlay = render_overlay_rgba(
                finding.heatmap, analysis.image_shape, analysis.heatmap_roi
            )
            (directory / f"{index}.png").write_bytes(encode_png(overlay))
            np.save(directory / f"{index}.npy", finding.heatmap)
            heatmap_url = f"{API_PREFIX}/ai/results/{result_id}/heatmaps/{index}"
        findings.append(
            {
                "index": index,
                "label": finding.label,
                "probability": round(finding.probability, 4),
                "positive": finding.positive,
                "calibrated": finding.calibrated,
                "heatmap_url": heatmap_url,
                "codes": codes_for(region, finding.label),
            }
        )

    return {
        "routing": {
            "region": region,
            "body_part": analysis.routing.body_part,
            "modality": analysis.routing.modality,
            "source": analysis.routing.source.value,
            "confidence": round(analysis.routing.confidence, 4),
            "notes": list(analysis.routing.notes),
        },
        "pipeline": analysis.pipeline,
        "model": {"name": analysis.model_name, "version": analysis.model_version},
        "image_shape": list(analysis.image_shape),
        "heatmap_roi": list(analysis.heatmap_roi),
        "findings": findings,
        "notes": list(analysis.notes),
        "report": build_report(region, findings),
        "deidentification": deid.as_dict(),
    }


async def save_result(
    session: AsyncSession,
    *,
    analysis: AnalysisResult,
    deid: DeidReport,
    heatmap_root: Path,
    created_by: str,
    source: str,
    study_uid: str | None,
    series_uid: str | None,
    sop_uid: str | None,
    patient_id: str | None,
) -> AiResult:
    result_id = str(uuid.uuid4())
    payload = build_payload(result_id, analysis, deid, heatmap_root)
    row = AiResult(
        id=result_id,
        created_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        created_by=created_by,
        source=source,
        study_instance_uid=study_uid,
        series_instance_uid=series_uid,
        sop_instance_uid=sop_uid,
        patient_id=patient_id,
        region=payload["routing"]["region"],
        pipeline=analysis.pipeline,
        model_name=analysis.model_name,
        model_version=analysis.model_version,
        severity=payload["report"]["severity"],
        payload=payload,
    )
    session.add(row)
    await session.commit()
    return row


def to_api(row: AiResult) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": row.created_at,
        "source": row.source,
        "study_instance_uid": row.study_instance_uid,
        "series_instance_uid": row.series_instance_uid,
        "sop_instance_uid": row.sop_instance_uid,
        "severity": row.severity,
        **row.payload,
    }
