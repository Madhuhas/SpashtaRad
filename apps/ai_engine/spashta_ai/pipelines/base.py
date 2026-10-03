"""Pipeline contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from spashta_ai.types import PipelineOutput, RoutingDecision


class Pipeline(ABC):
    """A model (or model ensemble) that turns one image into findings."""

    #: Stable identifier recorded with every result for traceability.
    name: str

    @abstractmethod
    def predict(
        self,
        image: np.ndarray,
        routing: RoutingDecision,
        *,
        explain: bool,
        threshold: float,
        max_heatmaps: int,
    ) -> PipelineOutput:
        """Run inference on a 2D float32 image in [0, 1]."""
