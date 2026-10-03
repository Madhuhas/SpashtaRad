"""Versioned REST API (``/api/v1``)."""

from fastapi import APIRouter

from app.api.v1 import ai, audit, cds, dicom, fhir

router = APIRouter(prefix="/api/v1")
router.include_router(dicom.router)
router.include_router(ai.router)
router.include_router(fhir.router)
router.include_router(cds.router, prefix="/cds")
router.include_router(audit.router)
