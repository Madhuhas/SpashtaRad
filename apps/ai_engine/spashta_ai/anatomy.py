"""Universal anatomy routing.

Resolution order:

1. ``(0018,0015) BodyPartExamined``                     - authoritative when present.
2. Free-text descriptions (Study/Series/Protocol)       - common fallback, since
   BodyPartExamined is type 3 (optional) and frequently empty in practice.
3. Image classifier (BiomedCLIP zero-shot)              - supplied by the engine.
4. Default to GENERAL.

This module is pure Python and has no model dependencies.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydicom.dataset import Dataset

from spashta_ai.types import AnatomyRegion, RoutingDecision, RoutingSource

# Modalities the projection-radiograph models were trained on.
RADIOGRAPH_MODALITIES = frozenset({"CR", "DX"})

# Modalities the engine will analyse at all. Every pipeline, the zero-shot
# prompts included, is written for projection radiographs; on CT, MR or
# ultrasound they return confident scores that mean nothing. A missing
# Modality is let through and judged on the image.
ANALYSABLE_MODALITIES = RADIOGRAPH_MODALITIES | {"RG"}

_CHEST_PARTS = frozenset({"CHEST", "THORAX", "LUNG", "LUNGS", "RIB", "RIBS"})

# MURA covers the upper extremity only. Lower-extremity studies are still MSK,
# but a MURA-trained model is out of distribution for them (see engine.py).
MURA_BODY_PARTS = frozenset({"ELBOW", "FINGER", "FOREARM", "HAND", "HUMERUS", "SHOULDER", "WRIST"})
_OTHER_MSK_PARTS = frozenset(
    {
        "ANKLE", "ARM", "CALCANEUS", "CLAVICLE", "EXTREMITY", "FEMUR", "FIBULA",
        "FOOT", "HEEL", "HIP", "KNEE", "LEG", "LOWERLEG", "PATELLA", "PELVIS",
        "RADIUS", "SCAPULA", "THUMB", "TIBIA", "TOE", "ULNA", "UPPERARM",
    }
)  # fmt: skip
_GENERAL_PARTS = frozenset(
    {
        "ABDOMEN", "BRAIN", "CSPINE", "HEAD", "LSPINE", "NECK", "SKULL", "SPINE",
        "SSPINE", "TSPINE", "TLSPINE", "CERVICALSPINE", "THORACICSPINE", "LUMBARSPINE",
        "SINUS", "ORBIT", "JAW", "KUB",
    }
)  # fmt: skip

# Word -> canonical body part, for scanning free-text descriptions.
_DESCRIPTION_KEYWORDS: dict[str, str] = {
    "CXR": "CHEST", "CHEST": "CHEST", "THORAX": "CHEST", "LUNG": "CHEST",
    "HAND": "HAND", "WRIST": "WRIST", "ELBOW": "ELBOW", "SHOULDER": "SHOULDER",
    "FINGER": "FINGER", "FOREARM": "FOREARM", "HUMERUS": "HUMERUS",
    "KNEE": "KNEE", "ANKLE": "ANKLE", "FOOT": "FOOT", "HIP": "HIP",
    "FEMUR": "FEMUR", "TIBIA": "TIBIA", "PELVIS": "PELVIS",
    "ABDOMEN": "ABDOMEN", "KUB": "ABDOMEN", "SKULL": "SKULL", "HEAD": "HEAD",
    "SPINE": "SPINE", "CERVICAL": "CSPINE", "LUMBAR": "LSPINE", "THORACIC": "TSPINE",
}  # fmt: skip

# An image classifier returns (canonical body part, confidence) or None.
AnatomyClassifier = Callable[[], tuple[str, float] | None]


def normalise_body_part(value: object) -> str | None:
    """Canonicalise a BodyPartExamined value: upper-case, alphanumerics only."""
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", str(value).upper())
    return cleaned or None


def region_for_body_part(body_part: str | None) -> AnatomyRegion | None:
    if body_part is None:
        return None
    if body_part in _CHEST_PARTS:
        return AnatomyRegion.CHEST
    if body_part in MURA_BODY_PARTS or body_part in _OTHER_MSK_PARTS:
        return AnatomyRegion.MSK
    if body_part in _GENERAL_PARTS:
        return AnatomyRegion.GENERAL
    return None


def route(ds: Dataset, classifier: AnatomyClassifier | None = None) -> RoutingDecision:
    """Decide which anatomical region (and therefore pipeline) a dataset belongs to.

    ``classifier`` is only invoked when the DICOM header is inconclusive.
    """
    modality = str(ds.get("Modality", "") or "").upper() or None
    notes: list[str] = []

    # 1. BodyPartExamined
    body_part = normalise_body_part(ds.get("BodyPartExamined"))
    region = region_for_body_part(body_part)
    if region is not None:
        return RoutingDecision(region, body_part, modality, RoutingSource.DICOM_TAGS, 1.0)
    if body_part is not None:
        notes.append(f"Unrecognised BodyPartExamined value '{body_part}'")

    # 2. Free-text descriptions
    described = _body_part_from_descriptions(ds)
    if described is not None:
        region = region_for_body_part(described)
        if region is not None:
            notes.append("Body part inferred from description text")
            return RoutingDecision(
                region, described, modality, RoutingSource.DESCRIPTION, 0.7, tuple(notes)
            )

    # 3. Image classifier
    if classifier is not None:
        predicted = classifier()
        if predicted is not None:
            label, confidence = predicted
            region = region_for_body_part(normalise_body_part(label))
            if region is not None:
                notes.append("Body part inferred by image classifier")
                return RoutingDecision(
                    region, label, modality, RoutingSource.CLASSIFIER, confidence, tuple(notes)
                )

    # 4. Default
    notes.append("Anatomy could not be determined; using general pipeline")
    return RoutingDecision(
        AnatomyRegion.GENERAL, body_part, modality, RoutingSource.DEFAULT, 0.0, tuple(notes)
    )


def _body_part_from_descriptions(ds: Dataset) -> str | None:
    text = " ".join(
        str(ds.get(keyword, "") or "")
        for keyword in ("StudyDescription", "SeriesDescription", "ProtocolName")
    ).upper()
    for word in re.findall(r"[A-Z]+", text):
        if word in _DESCRIPTION_KEYWORDS:
            return _DESCRIPTION_KEYWORDS[word]
    return None
