"""Rule-based draft report builder.

Turns model findings into a structured draft (findings list, impression,
coded concepts). Output is deterministic and template-driven: no language
model is involved, so every sentence is traceable to a rule in this file.

The draft is decision support for a radiologist, never a final report.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

DISCLAIMER = (
    "AI-generated draft for research and decision support only. Not a diagnosis. "
    "Must be reviewed and confirmed by a qualified radiologist."
)

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


@lru_cache
def load_terminology() -> dict[str, Any]:
    text = resources.files("app.data").joinpath("terminology.json").read_text(encoding="utf-8")
    return json.loads(text)


def lookup_concept(region: str, label: str) -> dict[str, Any] | None:
    return load_terminology().get(region, {}).get(label)


def codes_for(region: str, label: str) -> dict[str, Any]:
    concept = lookup_concept(region, label) or {}
    return {"icd10": concept.get("icd10"), "snomed": concept.get("snomed")}


def _qualifier(probability: float, calibrated: bool) -> str:
    if not calibrated:
        return "Zero-shot screen suggests"
    if probability >= 0.85:
        return "High-probability"
    if probability >= 0.70:
        return "Probable"
    return "Possible"


# Chest pattern rules: (labels, minimum number present, impression sentence).
_CHEST_PATTERNS: tuple[tuple[frozenset[str], int, str], ...] = (
    (
        frozenset({"Pneumothorax"}),
        1,
        "Possible pneumothorax: prompt radiologist review is recommended.",
    ),
    (
        frozenset({"Cardiomegaly", "Edema", "Effusion"}),
        2,
        "Combination of findings may reflect a fluid-overload / congestive pattern; "
        "correlate clinically.",
    ),
    (
        frozenset({"Consolidation", "Pneumonia", "Infiltration", "Lung Opacity"}),
        2,
        "Airspace opacity pattern; infection is a consideration in the appropriate "
        "clinical setting.",
    ),
    (
        frozenset({"Nodule", "Mass", "Lung Lesion"}),
        1,
        "Focal pulmonary lesion suspected; consider follow-up imaging per institutional "
        "guidelines.",
    ),
)


def build_report(region: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a draft report from API-shaped findings (label, probability, positive, calibrated)."""
    positives = [f for f in findings if f["positive"]]

    findings_lines: list[str] = []
    impression: list[str] = []
    codes: list[dict[str, Any]] = []
    severity = "info"

    for finding in positives:
        concept = lookup_concept(region, finding["label"]) or {}
        phrase = concept.get("phrase", finding["label"].lower())
        qualifier = _qualifier(finding["probability"], finding["calibrated"])
        score = f"{finding['probability']:.0%}"
        score_kind = "probability" if finding["calibrated"] else "similarity score"

        findings_lines.append(f"{finding['label']}: {score_kind} {score}.")
        impression.append(f"{qualifier} {phrase}.")

        finding_severity = concept.get("severity", "warning")
        if SEVERITY_ORDER[finding_severity] > SEVERITY_ORDER[severity]:
            severity = finding_severity

        for system in ("icd10", "snomed"):
            code = concept.get(system)
            if code and not any(c["system"] == system and c["code"] == code["code"] for c in codes):
                codes.append({"system": system, **code, "finding": finding["label"]})

    if region == "CHEST":
        present = {f["label"] for f in positives}
        impression += [
            sentence
            for labels, minimum, sentence in _CHEST_PATTERNS
            if len(labels & present) >= minimum
        ]

    if not positives:
        findings_lines.append("No finding exceeded the model's operating threshold.")
        impression.append("No AI-detected abnormality above the operating threshold.")

    return {
        "findings_text": findings_lines,
        "impression": impression,
        "codes": codes,
        "severity": severity,
        "disclaimer": DISCLAIMER,
    }
