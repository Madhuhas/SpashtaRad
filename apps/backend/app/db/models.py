"""ORM models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditEvent(Base):
    """Append-only, hash-chained audit trail (see ``services.audit``)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # ISO-8601 UTC string: stored verbatim so the hashed value survives a DB
    # round trip regardless of the backend's datetime handling.
    timestamp: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(16))
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(255), index=True)
    patient_ref: Mapped[str | None] = mapped_column(String(255), index=True)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    previous_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class AiResult(Base):
    """A completed AI analysis of one DICOM instance."""

    __tablename__ = "ai_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    created_by: Mapped[str] = mapped_column(String(255))
    # "pacs" results can be re-fetched and exported; "upload" results cannot.
    source: Mapped[str] = mapped_column(String(16))
    study_instance_uid: Mapped[str | None] = mapped_column(String(64), index=True)
    series_instance_uid: Mapped[str | None] = mapped_column(String(64))
    sop_instance_uid: Mapped[str | None] = mapped_column(String(64))
    # PHI: the PACS PatientID, kept so CDS Hooks can match an EHR patient (by
    # MRN) to imaging results. Protect this table like the PACS itself.
    patient_id: Mapped[str | None] = mapped_column(String(64), index=True)
    region: Mapped[str] = mapped_column(String(16))
    pipeline: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(128))
    model_version: Mapped[str] = mapped_column(String(128))
    # Highest clinical severity among positive findings: info | warning | critical.
    severity: Mapped[str] = mapped_column(String(16))
    # Full API payload (routing, findings, report).
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (Index("ix_ai_results_patient_created", "patient_id", "created_at"),)


class SmartSession(Base):
    """Server-side SMART on FHIR launch state and tokens."""

    __tablename__ = "smart_sessions"

    # Opaque random id; also the value of the browser session cookie.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issuer: Mapped[str] = mapped_column(String(512))
    token_endpoint: Mapped[str] = mapped_column(String(512))
    code_verifier: Mapped[str] = mapped_column(String(128))
    # Populated after the authorisation code is exchanged.
    # NOTE: stored in clear text; encrypt at rest (or keep in a secrets-backed
    # session store) before using against a production EHR.
    access_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[str | None] = mapped_column(String(40))
    patient_fhir_id: Mapped[str | None] = mapped_column(String(128))
