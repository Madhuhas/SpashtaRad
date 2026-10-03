"""CDS Hooks card generation from stored AI results."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

from app.db.models import AiResult
from app.services.reporting import DISCLAIMER, SEVERITY_ORDER

SOURCE_LABEL = "SpashtaRad AI"
_MAX_SUMMARY = 140  # CDS Hooks: summary must be under 140 characters
_PATIENT_PREFETCH = {"patient": "Patient/{{context.patientId}}"}

# CDS Hooks binds each service to exactly one hook, so the two triggers are
# exposed as two services sharing one implementation.
SERVICES: list[dict[str, Any]] = [
    {
        "hook": "patient-view",
        "id": "spashtarad-imaging-alert",
        "title": "SpashtaRad AI imaging alerts",
        "description": "Surfaces AI-detected imaging findings when a patient chart is opened.",
        "prefetch": _PATIENT_PREFETCH,
    },
    {
        "hook": "order-select",
        "id": "spashtarad-imaging-order-select",
        "title": "SpashtaRad AI prior imaging findings",
        "description": "Shows existing AI imaging findings while an order is being selected.",
        "prefetch": _PATIENT_PREFETCH,
    },
]
SERVICES_BY_ID = {service["id"]: service for service in SERVICES}


def patient_identifiers(
    patient: dict[str, Any] | None, context_patient_id: str | None
) -> list[str]:
    """Values that may equal the DICOM PatientID: MRN-type identifiers first."""
    values: list[str] = []
    for identifier in (patient or {}).get("identifier", []):
        value = identifier.get("value")
        if not value:
            continue
        codes = {c.get("code") for c in identifier.get("type", {}).get("coding", [])}
        if "MR" in codes:
            values.insert(0, value)
        else:
            values.append(value)
    if context_patient_id:
        values.append(context_patient_id)
    # De-duplicate, preserving priority order.
    return list(dict.fromkeys(values))


def build_cards(results: list[AiResult], *, hook: str, viewer_url: str) -> list[dict[str, Any]]:
    """One card per study with notable findings, most severe first.

    Studies whose AI result is unremarkable produce no card: an empty response
    is the correct CDS Hooks behaviour when there is nothing to act on.
    """
    notable = [r for r in results if SEVERITY_ORDER[r.severity] > SEVERITY_ORDER["info"]]
    notable.sort(key=lambda r: (SEVERITY_ORDER[r.severity], r.created_at), reverse=True)

    prefix = "Prior AI imaging findings" if hook == "order-select" else "AI imaging findings"
    cards = []
    for result in notable:
        positives = [f for f in result.payload["findings"] if f["positive"]]
        labels = ", ".join(f["label"] for f in positives)
        summary = f"{prefix}: {labels}"
        if len(summary) > _MAX_SUMMARY:
            summary = summary[: _MAX_SUMMARY - 1].rstrip() + "…"

        report = result.payload["report"]
        detail_lines = [f"**Draft impression** ({result.created_at[:10]})", ""]
        detail_lines += [f"- {line}" for line in report["impression"]]
        detail_lines += [
            "",
            f"Model: `{result.model_name}` ({result.model_version})",
            "",
            f"_{DISCLAIMER}_",
        ]

        card: dict[str, Any] = {
            "uuid": str(uuid.uuid4()),
            "summary": summary,
            "indicator": result.severity,
            "detail": "\n".join(detail_lines),
            "source": {"label": SOURCE_LABEL, "url": viewer_url},
        }
        if result.study_instance_uid:
            card["links"] = [
                {
                    "label": "Open study in SpashtaRad viewer",
                    "url": f"{viewer_url.rstrip('/')}/?study={quote(result.study_instance_uid)}",
                    "type": "absolute",
                }
            ]
        cards.append(card)
    return cards
