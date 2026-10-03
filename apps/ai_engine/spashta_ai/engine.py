"""Analysis engine: anatomy routing + lazy pipeline registry."""

from __future__ import annotations

import importlib.util
import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from pydicom.dataset import Dataset

from spashta_ai import anatomy
from spashta_ai.errors import ModelUnavailableError
from spashta_ai.pipelines.base import Pipeline
from spashta_ai.preprocessing import to_display_array
from spashta_ai.types import AnalysisResult, AnatomyRegion, RoutingDecision, RoutingSource

logger = logging.getLogger(__name__)

CHEST_PIPELINE = "chest-xrv"
MSK_PIPELINE = "msk-mura"
ZERO_SHOT_PIPELINE = "biomedclip-zero-shot"


@dataclass(frozen=True)
class EngineConfig:
    device: str = "cpu"
    # Score at or above which a finding is reported as positive.
    positive_threshold: float = 0.5
    # Upper bound on Grad-CAM maps computed per image.
    max_heatmaps: int = 5
    chest_weights: str = "densenet121-res224-all"
    # Path to a DenseNet-169 MURA checkpoint; None disables the MSK model.
    msk_weights_path: Path | None = None
    # Allow BiomedCLIP for anatomy detection and non-chest anomaly screening.
    enable_zero_shot: bool = True


class AnalysisEngine:
    """Routes DICOM images to model pipelines.

    Thread-safe: models are loaded once, on first use, under a lock. Inference
    itself is not serialised here; callers decide their own concurrency.
    """

    def __init__(self, config: EngineConfig | None = None) -> None:
        self._config = config or EngineConfig()
        self._lock = threading.Lock()
        self._pipelines: dict[str, Pipeline] = {}
        self._clip = None

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        """Report which pipelines can run, without loading any model."""
        torch_ok = _installed("torch")
        msk_path = self._config.msk_weights_path
        return {
            "device": self._config.device,
            "pipelines": {
                CHEST_PIPELINE: {
                    "available": torch_ok and _installed("torchxrayvision"),
                    "loaded": CHEST_PIPELINE in self._pipelines,
                },
                MSK_PIPELINE: {
                    "available": torch_ok
                    and _installed("torchvision")
                    and msk_path is not None
                    and msk_path.is_file(),
                    "loaded": MSK_PIPELINE in self._pipelines,
                },
                ZERO_SHOT_PIPELINE: {
                    "available": self._zero_shot_available(),
                    "loaded": ZERO_SHOT_PIPELINE in self._pipelines,
                },
            },
        }

    # ----------------------------------------------------------------- routing

    def route(self, ds: Dataset) -> RoutingDecision:
        """Header-only routing. Never loads a model."""
        return anatomy.route(ds)

    # ---------------------------------------------------------------- analysis

    def analyze(
        self,
        ds: Dataset,
        *,
        explain: bool = True,
        preliminary: RoutingDecision | None = None,
    ) -> AnalysisResult:
        """Route and analyse one image.

        Args:
            ds: Dataset to analyse (may already be de-identified).
            explain: Compute Grad-CAM heatmaps for positive findings.
            preliminary: A :meth:`route` decision taken on the original header.
                De-identification strips the free-text descriptions that
                routing can fall back on, so callers that de-identify first
                should route beforehand and pass the decision in.
        """
        image = to_display_array(ds)

        if preliminary is not None and preliminary.source is not RoutingSource.DEFAULT:
            routing = preliminary
        else:
            classifier = None
            if self._zero_shot_available():
                classifier = lambda: self._get_clip().classify_anatomy(image)  # noqa: E731
            routing = anatomy.route(ds, classifier)

        pipeline_name, notes = self._select_pipeline(routing)
        pipeline = self._get_pipeline(pipeline_name)
        logger.info(
            "analysis routed",
            extra={"region": routing.region.value, "pipeline": pipeline_name},
        )

        output = pipeline.predict(
            image,
            routing,
            explain=explain,
            threshold=self._config.positive_threshold,
            max_heatmaps=self._config.max_heatmaps,
        )
        return AnalysisResult(
            routing=replace(routing, notes=routing.notes + tuple(notes)),
            pipeline=pipeline_name,
            model_name=output.model_name,
            model_version=output.model_version,
            findings=output.findings,
            heatmap_roi=output.heatmap_roi,
            image_shape=(int(image.shape[0]), int(image.shape[1])),
            notes=output.notes,
        )

    # ---------------------------------------------------------------- internal

    def _select_pipeline(self, routing: RoutingDecision) -> tuple[str, list[str]]:
        notes: list[str] = []
        is_radiograph = (
            routing.modality is None or routing.modality in anatomy.RADIOGRAPH_MODALITIES
        )

        if routing.region is AnatomyRegion.CHEST:
            if is_radiograph:
                return CHEST_PIPELINE, notes
            notes.append(f"Chest model supports CR/DX only (modality {routing.modality})")

        elif routing.region is AnatomyRegion.MSK:
            msk_path = self._config.msk_weights_path
            if not is_radiograph:
                notes.append(f"MSK model supports CR/DX only (modality {routing.modality})")
            elif routing.body_part not in anatomy.MURA_BODY_PARTS:
                notes.append(
                    f"MSK model is trained on upper-extremity radiographs only "
                    f"('{routing.body_part}' is out of distribution)"
                )
            elif msk_path is None or not msk_path.is_file():
                notes.append("MSK model weights are not configured")
            else:
                return MSK_PIPELINE, notes

        if not self._zero_shot_available():
            raise ModelUnavailableError(
                "No pipeline available for this image: "
                + ("; ".join(notes) or "zero-shot inference is disabled or not installed")
            )
        if notes:
            notes.append("Falling back to zero-shot inference")
        return ZERO_SHOT_PIPELINE, notes

    def _zero_shot_available(self) -> bool:
        return self._config.enable_zero_shot and _installed("torch") and _installed("open_clip")

    def _get_clip(self):
        with self._lock:
            if self._clip is None:
                from spashta_ai.pipelines.zero_shot import BiomedClip

                logger.info("loading BiomedCLIP")
                self._clip = BiomedClip(self._config.device)
            return self._clip

    def _get_pipeline(self, name: str) -> Pipeline:
        # The zero-shot pipeline shares the CLIP model with anatomy detection;
        # resolve it outside the registry lock to avoid re-entrant locking.
        clip = self._get_clip() if name == ZERO_SHOT_PIPELINE else None
        with self._lock:
            pipeline = self._pipelines.get(name)
            if pipeline is None:
                logger.info("loading pipeline", extra={"pipeline": name})
                pipeline = self._build_pipeline(name, clip)
                self._pipelines[name] = pipeline
            return pipeline

    def _build_pipeline(self, name: str, clip) -> Pipeline:
        try:
            if name == CHEST_PIPELINE:
                from spashta_ai.pipelines.chest_xrv import ChestXrvPipeline

                return ChestXrvPipeline(self._config.chest_weights, self._config.device)
            if name == MSK_PIPELINE:
                from spashta_ai.pipelines.msk_mura import MskMuraPipeline

                assert self._config.msk_weights_path is not None
                return MskMuraPipeline(self._config.msk_weights_path, self._config.device)
            if name == ZERO_SHOT_PIPELINE:
                from spashta_ai.pipelines.zero_shot import ZeroShotPipeline

                return ZeroShotPipeline(clip)
        except ImportError as exc:
            raise ModelUnavailableError(
                f"Pipeline '{name}' requires model runtimes that are not installed "
                f"({exc.name}). Install apps/ai_engine/requirements.txt."
            ) from exc
        raise ValueError(f"Unknown pipeline '{name}'")


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None
