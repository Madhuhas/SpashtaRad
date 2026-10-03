"""Bridge between the API and the AI engine.

Responsibilities:

* enforce the privacy boundary - the engine only ever receives de-identified
  datasets;
* keep CPU/GPU-bound inference off the event loop;
* bound concurrency so parallel requests cannot exhaust memory.
"""

from __future__ import annotations

from typing import Protocol

import anyio
from pydicom.dataset import Dataset
from spashta_ai import AnalysisEngine, AnalysisResult, EngineConfig, RoutingDecision

from app.core.config import Settings
from app.services.deid import Deidentifier, DeidReport


class Engine(Protocol):
    """The subset of ``AnalysisEngine`` the backend depends on."""

    def status(self) -> dict: ...

    def route(self, ds: Dataset) -> RoutingDecision: ...

    def analyze(
        self, ds: Dataset, *, explain: bool = True, preliminary: RoutingDecision | None = None
    ) -> AnalysisResult: ...


class InferenceService:
    def __init__(self, settings: Settings, engine: Engine | None = None) -> None:
        self._engine: Engine = engine or AnalysisEngine(
            EngineConfig(
                device=settings.ai_device,
                positive_threshold=settings.ai_positive_threshold,
                max_heatmaps=settings.ai_max_heatmaps,
                msk_weights_path=settings.ai_msk_weights_path,
                enable_zero_shot=settings.ai_enable_zero_shot,
            )
        )
        self._deidentifier = Deidentifier(settings.deid_uid_salt.get_secret_value())
        # One inference at a time: models are large and a single run already
        # uses every core. Scale out with more workers, not more threads.
        self._limiter = anyio.CapacityLimiter(1)

    def status(self) -> dict:
        return self._engine.status()

    def route(self, ds: Dataset) -> RoutingDecision:
        return self._engine.route(ds)

    async def analyze(self, ds: Dataset, *, explain: bool) -> tuple[AnalysisResult, DeidReport]:
        """De-identify ``ds`` and run it through the engine in a worker thread."""
        # Routing may rely on free-text descriptions that de-identification
        # removes, so decide on the original header first.
        preliminary = self._engine.route(ds)

        def work() -> tuple[AnalysisResult, DeidReport]:
            clean, report = self._deidentifier.deidentify(ds)
            return self._engine.analyze(clean, explain=explain, preliminary=preliminary), report

        return await anyio.to_thread.run_sync(work, limiter=self._limiter)
