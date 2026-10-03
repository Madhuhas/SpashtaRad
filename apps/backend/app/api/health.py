"""Liveness and readiness probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text

from app import __version__
from app.api.deps import get_inference, get_pacs
from app.services.dicomweb import DicomWebClient
from app.services.inference import InferenceService

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    """The process is up."""
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
async def readiness(
    request: Request,
    response: Response,
    pacs: DicomWebClient = Depends(get_pacs),
    inference: InferenceService = Depends(get_inference),
) -> dict[str, Any]:
    """Dependencies are reachable. The database is required; PACS and AI are reported."""
    try:
        async with request.app.state.database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        database_ok = True
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        database_ok = False

    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if database_ok else "unavailable",
        "database": database_ok,
        "pacs": await pacs.ping(),
        "ai": inference.status(),
    }
