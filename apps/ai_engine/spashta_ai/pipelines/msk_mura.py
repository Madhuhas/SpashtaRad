"""Musculoskeletal abnormality pipeline (DenseNet-169, MURA-style).

There is no officially published, freely redistributable MURA checkpoint, so
this pipeline loads weights that you train (or obtain) yourself. It expects a
torchvision ``densenet169`` whose classifier is a single-logit ``Linear`` layer
("abnormal" vs "normal"), the architecture from the MURA paper
(Rajpurkar et al., 2017).

When no weights are configured the engine falls back to zero-shot inference.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np

from spashta_ai.errors import ModelUnavailableError
from spashta_ai.explain.gradcam import grad_cam
from spashta_ai.pipelines.base import Pipeline
from spashta_ai.types import Finding, PipelineOutput, RoutingDecision

_INPUT_SIZE = 320
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


class MskMuraPipeline(Pipeline):
    name = "msk-mura"

    def __init__(self, weights_path: Path, device: str = "cpu") -> None:
        import torch
        import torchvision

        if not weights_path.is_file():
            raise ModelUnavailableError(f"MSK weights not found at {weights_path}")

        self._torch = torch
        self._device = torch.device(device)
        self._version = f"sha256:{_file_digest(weights_path)[:12]}"

        model = torchvision.models.densenet169(weights=None)
        model.classifier = torch.nn.Linear(model.classifier.in_features, 1)
        # weights_only=True refuses pickled code, so an untrusted checkpoint
        # cannot execute arbitrary Python on load.
        state = torch.load(weights_path, map_location=self._device, weights_only=True)
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        state = {key.removeprefix("module."): value for key, value in state.items()}
        model.load_state_dict(state)
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        self._model = model.to(self._device).eval()

        self._mean = torch.tensor(_IMAGENET_MEAN, device=self._device).view(1, 3, 1, 1)
        self._std = torch.tensor(_IMAGENET_STD, device=self._device).view(1, 3, 1, 1)

    def predict(
        self,
        image: np.ndarray,
        routing: RoutingDecision,
        *,
        explain: bool,
        threshold: float,
        max_heatmaps: int,
    ) -> PipelineOutput:
        torch = self._torch
        functional = torch.nn.functional
        resized = cv2.resize(image, (_INPUT_SIZE, _INPUT_SIZE), interpolation=cv2.INTER_AREA)
        x = torch.from_numpy(resized).float().to(self._device)[None, None].repeat(1, 3, 1, 1)
        x = (x - self._mean) / self._std

        # A single forward pass serves both the score and (optionally) Grad-CAM.
        with torch.set_grad_enabled(explain):
            if explain:
                x = x.requires_grad_(True)
            activations = functional.relu(self._model.features(x))
            pooled = functional.adaptive_avg_pool2d(activations, (1, 1)).flatten(1)
            logit = self._model.classifier(pooled)[0, 0]
            probability = float(torch.sigmoid(logit))
            positive = probability >= threshold
            heatmap = (
                grad_cam(activations, logit) if explain and positive and max_heatmaps else None
            )

        finding = Finding(
            label="Musculoskeletal abnormality",
            probability=probability,
            positive=positive,
            calibrated=True,
            heatmap=heatmap,
        )
        return PipelineOutput(
            model_name="densenet169-mura",
            model_version=self._version,
            findings=[finding],
            notes=[
                "Study-level abnormality detection; does not localise or classify fracture type"
            ],
        )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
