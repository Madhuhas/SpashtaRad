"""Unit tests for services that need no HTTP app."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydicom.dataset import Dataset

from app.core.config import Settings
from app.services.deid import Deidentifier
from app.services.dicomweb import encode_multipart_related, parse_multipart_related, validate_uid
from app.services.reporting import build_report
from tests.conftest import make_dataset

# ------------------------------------------------------------ de-identification


def test_deidentification_removes_direct_identifiers():
    original = make_dataset()
    clean, report = Deidentifier("salt").deidentify(original)

    assert clean.PatientName == "ANONYMOUS"
    assert clean.PatientID not in ("", original.PatientID)
    assert clean.PatientBirthDate == ""
    assert clean.ReferringPhysicianName == ""
    for keyword in ("PatientAddress", "InstitutionName", "AccessionNumber", "StudyDescription"):
        assert keyword not in clean
    assert clean.PatientIdentityRemoved == "YES"
    assert report.elements_removed >= 4


def test_deidentification_keeps_only_year_of_dates_and_caps_age():
    clean, _ = Deidentifier("salt").deidentify(make_dataset())
    assert clean.StudyDate == "20240101"
    assert clean.StudyTime == "000000"
    assert clean.PatientAge == "090Y"  # 94 -> "90 or older"


def test_deidentification_preserves_what_the_models_need():
    original = make_dataset()
    clean, _ = Deidentifier("salt").deidentify(original)
    assert clean.Modality == "DX" and clean.BodyPartExamined == "CHEST"
    assert clean.PixelData == original.PixelData
    assert clean.SOPClassUID == original.SOPClassUID  # standard UIDs are not remapped


def test_uids_are_remapped_deterministically_per_salt():
    original = make_dataset()
    first, report = Deidentifier("salt").deidentify(original)
    second, _ = Deidentifier("salt").deidentify(original)
    other, _ = Deidentifier("different").deidentify(original)

    assert first.StudyInstanceUID != original.StudyInstanceUID
    assert first.StudyInstanceUID.startswith("2.25.") and len(first.StudyInstanceUID) <= 64
    assert first.StudyInstanceUID == second.StudyInstanceUID
    assert first.StudyInstanceUID != other.StudyInstanceUID
    assert first.file_meta.MediaStorageSOPInstanceUID == first.SOPInstanceUID
    assert report.uids_remapped >= 3


def test_deidentification_reaches_into_sequences_and_private_tags():
    original = make_dataset()
    item = Dataset()
    item.PersonName = "HIDDEN^NAME"
    item.ContentDate = "20240315"
    original.ContentSequence = [item]
    original.add_new(0x00111001, "LO", "private secret")

    clean, _ = Deidentifier("salt").deidentify(original)
    assert clean.ContentSequence[0].PersonName == ""
    assert clean.ContentSequence[0].ContentDate == "20240101"
    assert (0x0011, 0x1001) not in clean
    # The caller's dataset is untouched.
    assert original.PatientName == "DOE^JANE"


def test_burnt_in_annotation_is_flagged():
    _, report = Deidentifier("salt").deidentify(make_dataset(BurnedInAnnotation="YES"))
    assert any("burnt-in text" in warning for warning in report.warnings)
    _, report = Deidentifier("salt").deidentify(make_dataset(BurnedInAnnotation="NO"))
    assert report.warnings == []


# ------------------------------------------------------------------- DICOMweb


def test_multipart_round_trip_preserves_binary_payloads():
    parts = [b"\x00\x01--not-a-boundary\r\n\r\n\xff", b"second\r\n"]
    body, content_type = encode_multipart_related(parts)
    assert parse_multipart_related(body, content_type) == parts


def test_multipart_rejects_non_multipart():
    with pytest.raises(ValueError):
        parse_multipart_related(b"{}", "application/json")


@pytest.mark.parametrize("bad", ["", "1.2.x", "../admin", "1..2", "1.2/3", "1" * 65])
def test_validate_uid_rejects_unsafe_values(bad):
    with pytest.raises(ValueError):
        validate_uid(bad)


def test_validate_uid_accepts_real_uid():
    assert validate_uid("1.2.840.113619.2.55.3") == "1.2.840.113619.2.55.3"


# ------------------------------------------------------------------ reporting


def _finding(label: str, probability: float, positive: bool = True, calibrated: bool = True):
    return {
        "label": label,
        "probability": probability,
        "positive": positive,
        "calibrated": calibrated,
    }


def test_report_codes_and_pattern_rules():
    report = build_report(
        "CHEST",
        [_finding("Cardiomegaly", 0.9), _finding("Effusion", 0.75), _finding("Nodule", 0.2, False)],
    )
    assert report["impression"][0] == "High-probability cardiomegaly."
    assert report["impression"][1] == "Probable pleural effusion."
    assert any("congestive pattern" in line for line in report["impression"])
    assert {
        "system": "icd10",
        "code": "I51.7",
        "display": "Cardiomegaly",
        "finding": "Cardiomegaly",
    } in report["codes"]
    assert any(c["system"] == "snomed" and c["code"] == "60046008" for c in report["codes"])
    assert report["severity"] == "warning"


def test_report_escalates_pneumothorax_and_handles_normal():
    assert build_report("CHEST", [_finding("Pneumothorax", 0.6)])["severity"] == "critical"
    normal = build_report("CHEST", [_finding("Effusion", 0.1, False)])
    assert normal["severity"] == "info" and normal["codes"] == []
    assert "No AI-detected abnormality" in normal["impression"][0]


def test_report_labels_uncalibrated_scores():
    report = build_report("MSK", [_finding("Fracture", 0.8, calibrated=False)])
    assert report["impression"] == ["Zero-shot screen suggests fracture."]
    assert "similarity score" in report["findings_text"][0]


# -------------------------------------------------------------- configuration


def test_production_refuses_insecure_defaults():
    with pytest.raises(ValidationError, match="Insecure production configuration"):
        Settings(_env_file=None, environment="production")


def test_production_accepts_hardened_configuration():
    Settings(
        _env_file=None,
        environment="production",
        auth_mode="oidc",
        cds_auth_mode="jwt",
        deid_uid_salt="a-private-random-value",
    )


def test_production_refuses_unauthenticated_cds_hooks():
    with pytest.raises(ValidationError, match="SPASHTA_CDS_AUTH_MODE=none is not allowed"):
        Settings(
            _env_file=None,
            environment="production",
            auth_mode="oidc",
            deid_uid_salt="a-private-random-value",
        )
