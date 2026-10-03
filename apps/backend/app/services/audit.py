"""Tamper-evident audit trail.

Every event stores the SHA-256 of its own content chained to the previous
event's hash. Editing or deleting any historical row breaks the chain, which
``AuditService.verify`` detects.

Audit writes fail closed: if an event cannot be recorded the exception
propagates and the audited operation is not performed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import request_id_var
from app.db.models import AuditEvent

GENESIS_HASH = "0" * 64


class AuditAction(StrEnum):
    DICOM_QUERY = "dicom.query"
    DICOM_RETRIEVE = "dicom.retrieve"
    DICOM_STORE = "dicom.store"
    DICOM_DEIDENTIFIED_EXPORT = "dicom.deidentified_export"
    AI_ANALYZE = "ai.analyze"
    AI_RESULT_READ = "ai.result_read"
    AI_EXPORT_TO_PACS = "ai.export_to_pacs"
    FHIR_LAUNCH = "fhir.launch"
    FHIR_PATIENT_READ = "fhir.patient_read"
    CDS_HOOK = "cds.hook"
    AUDIT_READ = "audit.read"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True)
class ChainVerification:
    valid: bool
    events_checked: int
    first_invalid_id: int | None = None


def _event_hash(previous_hash: str, content: dict[str, Any]) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((previous_hash + canonical).encode("utf-8")).hexdigest()


def _hashed_content(event: AuditEvent) -> dict[str, Any]:
    return {
        "timestamp": event.timestamp,
        "actor": event.actor,
        "action": event.action,
        "outcome": event.outcome,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "patient_ref": event.patient_ref,
        "source_ip": event.source_ip,
        "request_id": event.request_id,
        "detail": event.detail,
    }


class AuditService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        # Serialises "read last hash -> insert" within this process. A
        # multi-process deployment must replace this with a database-level
        # lock (e.g. pg_advisory_xact_lock) to keep a single linear chain.
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        actor: str,
        action: AuditAction,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        resource_type: str | None = None,
        resource_id: str | None = None,
        patient_ref: str | None = None,
        source_ip: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append an event in its own transaction, independent of the caller's."""
        event = AuditEvent(
            timestamp=dt.datetime.now(dt.UTC).isoformat(timespec="microseconds"),
            actor=actor,
            action=action.value,
            outcome=outcome.value,
            resource_type=resource_type,
            resource_id=resource_id,
            patient_ref=patient_ref,
            source_ip=source_ip,
            request_id=request_id_var.get(),
            detail=detail,
        )
        async with self._lock, self._sessions() as session:
            previous = await session.scalar(
                select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1)
            )
            event.previous_hash = previous or GENESIS_HASH
            event.hash = _event_hash(event.previous_hash, _hashed_content(event))
            session.add(event)
            await session.commit()
        return event

    async def search(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
        patient_ref: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit).offset(offset)
        if actor:
            query = query.where(AuditEvent.actor == actor)
        if action:
            query = query.where(AuditEvent.action == action)
        if resource_id:
            query = query.where(AuditEvent.resource_id == resource_id)
        if patient_ref:
            query = query.where(AuditEvent.patient_ref == patient_ref)
        async with self._sessions() as session:
            return list((await session.scalars(query)).all())

    async def verify(self) -> ChainVerification:
        """Recompute the whole chain and report the first inconsistent event."""
        expected_previous = GENESIS_HASH
        checked = 0
        async with self._sessions() as session:
            stream = await session.stream_scalars(select(AuditEvent).order_by(AuditEvent.id))
            async for event in stream:
                checked += 1
                recomputed = _event_hash(event.previous_hash, _hashed_content(event))
                if event.previous_hash != expected_previous or event.hash != recomputed:
                    return ChainVerification(False, checked, event.id)
                expected_previous = event.hash
        return ChainVerification(True, checked)
