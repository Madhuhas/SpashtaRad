"""Chest radiograph pipeline (TorchXRayVision DenseNet-121)."""

from __future__ import annotations

import cv2
import numpy as np

from spashta_ai.explain.gradcam import grad_cam
from spashta_ai.pipelines.base import Pipeline
from spashta_ai.preprocessing import center_crop_square
from spashta_ai.types import Finding, PipelineOutput, RoutingDecision

_INPUT_SIZE = 224


class ChestXrvPipeline(Pipeline):
    """Multi-label thoracic pathology classifier with Grad-CAM explanations.

    The ``*-all`` weights are trained on the union of NIH ChestX-ray14, CheXpert,
    MIMIC-CXR, PadChest and others, and emit scores normalised so that 0.5 is
    each pathology's operating point.
    """

    name = "chest-xrv"

    def __init__(self, weights: str = "densenet121-res224-all", device: str = "cpu") -> None:
        import torch
        import torchxrayvision as xrv

        self._torch = torch
        self._device = torch.device(device)
        self._weights = weights
        self._version = f"torchxrayvision-{xrv.__version__}"
        self._model = xrv.models.DenseNet(weights=weights).to(self._device).eval()
        for parameter in self._model.parameters():
            parameter.requires_grad_(False)

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
        crop, roi = center_crop_square(image)
        resized = cv2.resize(crop, (_INPUT_SIZE, _INPUT_SIZE), interpolation=cv2.INTER_AREA)
        # TorchXRayVision models expect intensities in [-1024, 1024].
        x = torch.from_numpy(resized * 2048.0 - 1024.0).float()[None, None].to(self._device)

        with torch.no_grad():
            scores = self._model(x)[0].cpu().numpy()

        # Some weight sets pad their label list with empty names.
        indexed = [(i, label) for i, label in enumerate(self._model.pathologies) if label]
        findings = {
            i: Finding(
                label=label.replace("_", " "),
                probability=float(scores[i]),
                positive=bool(scores[i] >= threshold),
                calibrated=True,
            )
            for i, label in indexed
        }

        if explain:
            targets = sorted(
                (i for i, f in findings.items() if f.positive),
                key=lambda i: findings[i].probability,
                reverse=True,
            )[:max_heatmaps]
            for i, cam in self._explain(x, targets).items():
                findings[i].heatmap = cam

        ordered = sorted(findings.values(), key=lambda f: f.probability, reverse=True)
        return PipelineOutput(
            model_name=self._weights,
            model_version=self._version,
            findings=ordered,
            heatmap_roi=roi,
        )

    def _explain(self, x, targets: list[int]) -> dict[int, np.ndarray]:
        """Grad-CAM on the last dense block for each target class index."""
        if not targets:
            return {}
        torch = self._torch
        functional = torch.nn.functional
        with torch.enable_grad():
            activations = functional.relu(self._model.features(x.clone().requires_grad_(True)))
            pooled = functional.adaptive_avg_pool2d(activations, (1, 1)).flatten(1)
            logits = self._model.classifier(pooled)
            return {
                index: grad_cam(activations, logits[0, index], retain_graph=n < len(targets) - 1)
                for n, index in enumerate(targets)
            }
