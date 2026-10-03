"""Plain data types shared between the engine, pipelines and callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

# Region of interest as fractions of the original image: (x0, y0, x1, y1).
NormalizedRoi = tuple[float, float, float, float]
FULL_FRAME: NormalizedRoi = (0.0, 0.0, 1.0, 1.0)


class AnatomyRegion(StrEnum):
    CHEST = "CHEST"
    MSK = "MSK"
    GENERAL = "GENERAL"


class RoutingSource(StrEnum):
    DICOM_TAGS = "dicom_tags"
    DESCRIPTION = "description_text"
    CLASSIFIER = "classifier"
    DEFAULT = "default"


@dataclass(frozen=True)
class RoutingDecision:
    """Which anatomical region an image belongs to, and how we decided."""

    region: AnatomyRegion
    body_part: str | None
    modality: str | None
    source: RoutingSource
    confidence: float
    notes: tuple[str, ...] = ()


@dataclass
class Finding:
    label: str
    probability: float
    positive: bool
    # False for zero-shot similarity scores, which are not calibrated
    # probabilities and must not be presented as such.
    calibrated: bool
    # Class activation map in [0, 1], in the coordinate space of the
    # pipeline's input ROI (see AnalysisResult.heatmap_roi). None if no
    # explanation was produced for this finding.
    heatmap: np.ndarray | None = None


@dataclass
class PipelineOutput:
    model_name: str
    model_version: str
    findings: list[Finding]
    heatmap_roi: NormalizedRoi = FULL_FRAME
    notes: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    routing: RoutingDecision
    pipeline: str
    model_name: str
    model_version: str
    findings: list[Finding]
    # Where heatmaps sit within the original image (pipelines may crop).
    heatmap_roi: NormalizedRoi
    # (rows, columns) of the analysed frame.
    image_shape: tuple[int, int]
    notes: list[str] = field(default_factory=list)
