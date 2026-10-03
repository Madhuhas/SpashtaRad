"""End-to-end model tests. Skipped unless the model runtimes are installed.

These download open weights on first run and exercise the real inference and
Grad-CAM code paths on a synthetic image. They verify plumbing (shapes, ranges,
routing), not diagnostic accuracy.
"""

import numpy as np
import pytest

from spashta_ai import AnalysisEngine, AnatomyRegion, EngineConfig, ModelUnavailableError

pytestmark = pytest.mark.models


def test_chest_pipeline_runs_with_gradcam(dicom_factory):
    pytest.importorskip("torch")
    pytest.importorskip("torchxrayvision")

    # Threshold 0 marks every pathology positive so Grad-CAM is always exercised.
    engine = AnalysisEngine(
        EngineConfig(positive_threshold=0.0, max_heatmaps=2, enable_zero_shot=False)
    )
    result = engine.analyze(dicom_factory(rows=300, cols=400, body_part="CHEST"))

    assert result.pipeline == "chest-xrv"
    assert result.routing.region is AnatomyRegion.CHEST
    assert result.image_shape == (300, 400)
    assert result.heatmap_roi == (0.125, 0.0, 0.875, 1.0)  # centred square crop
    assert len(result.findings) >= 14
    assert all(0.0 <= f.probability <= 1.0 for f in result.findings)

    heatmaps = [f.heatmap for f in result.findings if f.heatmap is not None]
    assert len(heatmaps) == 2
    for heatmap in heatmaps:
        assert heatmap.ndim == 2
        assert 0.0 <= float(heatmap.min()) and float(heatmap.max()) <= 1.0
        assert np.isfinite(heatmap).all()


def test_msk_without_weights_or_zero_shot_is_unavailable(dicom_factory):
    engine = AnalysisEngine(EngineConfig(enable_zero_shot=False))
    with pytest.raises(ModelUnavailableError, match="weights are not configured"):
        engine.analyze(dicom_factory(body_part="WRIST"))


def test_zero_shot_pipeline_runs(dicom_factory):
    pytest.importorskip("torch")
    pytest.importorskip("open_clip")

    engine = AnalysisEngine(EngineConfig())
    result = engine.analyze(dicom_factory(body_part="KNEE"))

    assert result.pipeline == "biomedclip-zero-shot"
    assert {f.label for f in result.findings} >= {"Fracture", "Dislocation"}
    assert all(not f.calibrated and f.heatmap is None for f in result.findings)
    assert any("out of distribution" in note for note in result.routing.notes)
