import pytest

from spashta_ai import AnalysisEngine, AnatomyRegion, RoutingSource
from spashta_ai.anatomy import normalise_body_part, route
from spashta_ai.errors import UnsupportedImageError


@pytest.mark.parametrize("modality", ["CT", "MR", "US", "OT"])
def test_engine_refuses_modalities_the_models_were_not_built_for(dicom_factory, modality):
    """Refused before any model is loaded, whatever the body part says."""
    with pytest.raises(UnsupportedImageError, match=f"this image is {modality}"):
        AnalysisEngine().analyze(dicom_factory(modality=modality, body_part="CHEST"))


@pytest.mark.parametrize(
    ("body_part", "region"),
    [
        ("CHEST", AnatomyRegion.CHEST),
        ("chest ", AnatomyRegion.CHEST),
        ("WRIST", AnatomyRegion.MSK),
        ("KNEE", AnatomyRegion.MSK),
        ("L-SPINE", AnatomyRegion.GENERAL),
        ("ABDOMEN", AnatomyRegion.GENERAL),
        ("SKULL", AnatomyRegion.GENERAL),
    ],
)
def test_routes_by_body_part_tag(dicom_factory, body_part, region):
    decision = route(dicom_factory(body_part=body_part))
    assert decision.region is region
    assert decision.source is RoutingSource.DICOM_TAGS
    assert decision.confidence == 1.0
    assert decision.modality == "DX"


def test_normalise_body_part():
    assert normalise_body_part(" l-spine ") == "LSPINE"
    assert normalise_body_part("") is None
    assert normalise_body_part(None) is None


def test_falls_back_to_description_text(dicom_factory):
    ds = dicom_factory(body_part=None, StudyDescription="XR Wrist 3 views LEFT")
    decision = route(ds)
    assert decision.region is AnatomyRegion.MSK
    assert decision.body_part == "WRIST"
    assert decision.source is RoutingSource.DESCRIPTION


def test_falls_back_to_classifier_when_header_is_silent(dicom_factory):
    ds = dicom_factory(body_part=None)
    decision = route(ds, classifier=lambda: ("KNEE", 0.83))
    assert decision.region is AnatomyRegion.MSK
    assert decision.source is RoutingSource.CLASSIFIER
    assert decision.confidence == pytest.approx(0.83)


def test_classifier_not_called_when_tag_present(dicom_factory):
    def classifier():
        raise AssertionError("classifier must not run when the header is conclusive")

    assert route(dicom_factory(body_part="CHEST"), classifier).region is AnatomyRegion.CHEST


def test_defaults_to_general(dicom_factory):
    decision = route(dicom_factory(body_part="WHOLEBODYXYZ"))
    assert decision.region is AnatomyRegion.GENERAL
    assert decision.source is RoutingSource.DEFAULT
    assert any("Unrecognised" in note for note in decision.notes)
