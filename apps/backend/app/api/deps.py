"""FastAPI dependencies shared by all routers.

Long-lived services live on ``app.state`` (created in the application
lifespan); these functions expose them to route handlers and keep handlers
trivially overridable in tests.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator

import httpx
import pydicom
from fastapi import HTTPException, Request, UploadFile, status
from pydicom.dataset import Dataset
from pydicom.errors import InvalidDicomError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.audit import AuditService
from app.services.cds_auth import CdsClientVerifier
from app.services.dicomweb import DicomWebClient
from app.services.inference import InferenceService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.sessions() as session:
        yield session


def get_audit(request: Request) -> AuditService:
    return request.app.state.audit


def get_pacs(request: Request) -> DicomWebClient:
    return request.app.state.pacs


def get_cds_verifier(request: Request) -> CdsClientVerifier:
    return request.app.state.cds_verifier


def get_inference(request: Request) -> InferenceService:
    return request.app.state.inference


def get_external_http(request: Request) -> httpx.AsyncClient:
    """HTTP client for third-party servers (EHR / FHIR)."""
    return request.app.state.external_http


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload fully, refusing bodies larger than ``max_bytes``."""
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File exceeds {max_bytes} bytes"
        )
    return data


def parse_dicom(data: bytes) -> Dataset:
    """Parse DICOM Part 10 bytes, mapping parse failures to HTTP 400."""
    try:
        return pydicom.dcmread(io.BytesIO(data))
    except (InvalidDicomError, EOFError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "File is not a valid DICOM Part 10 object"
        ) from exc
