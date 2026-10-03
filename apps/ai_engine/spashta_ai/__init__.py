"""SpashtaRad AI engine.

Public surface:

* :class:`AnalysisEngine` - routes a DICOM dataset to a model pipeline and runs it.
* :class:`EngineConfig`   - runtime configuration (device, thresholds, weights).
* result types            - :class:`AnalysisResult`, :class:`Finding`, :class:`RoutingDecision`.

Importing this package never imports torch; model runtimes are loaded lazily
the first time a pipeline is used.
"""

from spashta_ai.engine import AnalysisEngine, EngineConfig
from spashta_ai.errors import ModelUnavailableError, UnsupportedImageError
from spashta_ai.types import (
    AnalysisResult,
    AnatomyRegion,
    Finding,
    RoutingDecision,
    RoutingSource,
)

__version__ = "0.1.0"

__all__ = [
    "AnalysisEngine",
    "AnalysisResult",
    "AnatomyRegion",
    "EngineConfig",
    "Finding",
    "ModelUnavailableError",
    "RoutingDecision",
    "RoutingSource",
    "UnsupportedImageError",
    "__version__",
]
