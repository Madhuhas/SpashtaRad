"""Application factory.

Run locally with::

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app import __version__
from app.api import dicomweb_proxy, health
from app.api.v1 import cds as cds_routes
from app.api.v1 import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.logging import RequestIdMiddleware, configure_logging
from app.db.database import Database
from app.services.audit import AuditService
from app.services.cds_auth import CdsClientVerifier
from app.services.dicomweb import DicomWebClient
from app.services.inference import Engine, InferenceService

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    pacs_transport: httpx.AsyncBaseTransport | None = None,
    external_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the application.

    The keyword arguments exist for tests: they replace the AI engine and the
    network transports for the PACS and for external (EHR) servers.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.heatmap_dir.mkdir(parents=True, exist_ok=True)
        database = Database(settings.database_url)
        await database.create_schema()

        password = settings.dicomweb_password
        app.state.database = database
        app.state.audit = AuditService(database.sessions)
        app.state.pacs = DicomWebClient(
            settings.dicomweb_url,
            username=settings.dicomweb_username,
            password=password.get_secret_value() if password else None,
            timeout=settings.dicomweb_timeout_seconds,
            transport=pacs_transport,
        )
        # Redirects stay disabled so an allow-listed EHR cannot bounce
        # requests (and bearer tokens) to another host.
        app.state.external_http = httpx.AsyncClient(
            timeout=20.0, follow_redirects=False, transport=external_transport
        )
        app.state.cds_verifier = CdsClientVerifier(
            app.state.external_http, settings.cds_trusted_clients
        )
        app.state.inference = InferenceService(settings, engine)
        app.state.retrieve_audit_cache = {}
        logger.info("startup complete", extra={"environment": settings.environment})
        try:
            yield
        finally:
            await app.state.external_http.aclose()
            await app.state.pacs.aclose()
            await database.dispose()

    app = FastAPI(
        title="SpashtaRad AI",
        version=__version__,
        description=(
            "DICOMweb gateway, AI analysis, SMART on FHIR and CDS Hooks. "
            "Research and decision-support software; not a medical device."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Deidentification-Report"],
    )
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(api_v1_router)
    app.include_router(dicomweb_proxy.router)
    # Conventional CDS Hooks location ({base}/cds-services) for EHR registration.
    app.include_router(cds_routes.router, include_in_schema=False)

    @app.get("/fhir/launch", include_in_schema=False)
    async def smart_launch_alias(request: Request) -> RedirectResponse:
        """Short launch URL for EHR app registration; forwards to the versioned API."""
        return RedirectResponse(f"/api/v1/fhir/launch?{request.url.query}", status_code=307)

    return app


app = create_app()
