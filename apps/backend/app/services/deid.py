"""DICOM header de-identification.

HIPAA Safe Harbor (45 CFR 164.514(b)(2)) lists 18 categories of identifier; it
does not map one-to-one onto DICOM tags. This module therefore combines:

* an explicit removal list drawn from the DICOM PS3.15 Annex E Basic
  Application Level Confidentiality Profile, and
* VR-driven rules that catch identifiers wherever they occur, including inside
  sequences: every person name, date, time, free-text and instance UID.

Limitations (surfaced as warnings on the result):

* Pixel data is NOT inspected. Burnt-in annotations (common in ultrasound,
  secondary captures and scanned documents) pass through unchanged.
* This is a Safe Harbor-oriented implementation, not an Expert Determination.
  Validate it against your own data before relying on it for disclosure.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
from dataclasses import dataclass, field

from pydicom.dataset import Dataset

# Explicitly removed attributes (PS3.15 Table E.1-1, action X or Z), beyond
# what the VR rules below already cover.
_REMOVE_KEYWORDS = frozenset(
    {
        # Patient
        "OtherPatientIDs", "OtherPatientIDsSequence", "IssuerOfPatientID",
        "PatientAddress", "PatientTelephoneNumbers", "PatientTelecomInformation",
        "MedicalRecordLocator", "EthnicGroup", "Occupation", "MilitaryRank",
        "BranchOfService", "PatientReligiousPreference", "CountryOfResidence",
        "RegionOfResidence", "PatientInsurancePlanCodeSequence", "PatientSize",
        "PatientWeight", "PatientState", "PregnancyStatus", "SmokingStatus",
        "ResponsiblePersonRole", "ResponsibleOrganization",
        # Institution / staff / equipment
        "InstitutionName", "InstitutionAddress", "InstitutionalDepartmentName",
        "InstitutionCodeSequence", "ReferringPhysicianAddress",
        "ReferringPhysicianTelephoneNumbers", "ReferringPhysicianIdentificationSequence",
        "PhysiciansOfRecordIdentificationSequence", "PerformingPhysicianIdentificationSequence",
        "OperatorIdentificationSequence", "StationName", "DeviceSerialNumber",
        "DeviceUID", "PlateID", "DetectorID", "CassetteID", "GantryID",
        # Study / visit identifiers and free text
        "AccessionNumber", "StudyID", "StudyDescription", "SeriesDescription",
        "ProtocolName", "AdmissionID", "AdmittingDiagnosesDescription",
        "AdmittingDiagnosesCodeSequence", "RequestingService", "RequestAttributesSequence",
        "RequestedProcedureDescription", "RequestedProcedureID", "PerformedProcedureStepID",
        "PerformedProcedureStepDescription", "ScheduledProcedureStepDescription",
        "PerformedLocation", "CurrentPatientLocation", "DerivationDescription",
        "IssuerOfAccessionNumberSequence", "TimezoneOffsetFromUTC",
        # Embedded content that may carry identifiers
        "IconImageSequence", "OriginalAttributesSequence", "DigitalSignaturesSequence",
        "EncryptedAttributesSequence",
    }
)  # fmt: skip

# Free-text VRs: removed wholesale, since their content cannot be vetted.
_FREE_TEXT_VRS = frozenset({"LT", "ST", "UT"})

# Standard DICOM UIDs (SOP classes, transfer syntaxes) identify no one.
_DICOM_UID_ROOT = "1.2.840.10008."

_SAFE_HARBOR_MAX_AGE = 89


@dataclass
class DeidReport:
    elements_removed: int = 0
    elements_modified: int = 0
    uids_remapped: int = 0
    private_tags_removed: bool = True
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "elements_removed": self.elements_removed,
            "elements_modified": self.elements_modified,
            "uids_remapped": self.uids_remapped,
            "private_tags_removed": self.private_tags_removed,
            "warnings": list(self.warnings),
        }


class Deidentifier:
    """De-identifies DICOM datasets with a keyed, deterministic pseudonymiser.

    The same input UID / PatientID always maps to the same output for a given
    salt, so de-identified studies remain internally consistent and linkable
    to each other, but not to the originals without the salt.
    """

    def __init__(self, salt: str) -> None:
        self._key = salt.encode("utf-8")

    def deidentify(self, ds: Dataset) -> tuple[Dataset, DeidReport]:
        """Return a de-identified deep copy of ``ds`` and a report of what changed."""
        report = DeidReport()
        self._collect_warnings(ds, report)

        out = copy.deepcopy(ds)
        out.remove_private_tags()
        self._scrub(out, report)

        # Top-level identity attributes: replaced rather than removed because
        # they are type 2 (must be present) in every image IOD.
        original_patient_id = str(ds.get("PatientID", "") or "")
        out.PatientName = "ANONYMOUS"
        out.PatientID = self.pseudonym(original_patient_id) if original_patient_id else ""
        out.PatientBirthDate = ""
        if "PatientAge" in out:
            out.PatientAge = _cap_age(str(out.PatientAge))

        out.PatientIdentityRemoved = "YES"
        out.DeidentificationMethod = "SpashtaRad PS3.15 Basic Profile subset + Safe Harbor dates"
        out.LongitudinalTemporalInformationModified = "MODIFIED"

        if getattr(out, "file_meta", None) is not None and "SOPInstanceUID" in out:
            out.file_meta.MediaStorageSOPInstanceUID = out.SOPInstanceUID
        return out, report

    def pseudonym(self, value: str) -> str:
        return self._digest(b"patient:" + value.encode("utf-8")).hex()[:16].upper()

    def remap_uid(self, uid: str) -> str:
        # 2.25.<integer> is the standard arc for UUID-derived UIDs; a 128-bit
        # integer keeps the result within the 64-character UID limit.
        number = int.from_bytes(self._digest(b"uid:" + uid.encode("ascii", "replace"))[:16], "big")
        return f"2.25.{number}"

    # ---------------------------------------------------------------- internal

    def _digest(self, message: bytes) -> bytes:
        return hmac.new(self._key, message, hashlib.sha256).digest()

    def _scrub(self, ds: Dataset, report: DeidReport) -> None:
        for element in list(ds):
            tag, vr = element.tag, element.VR

            # Curve (50xx) and overlay (60xx) groups can hold burnt-in text.
            if (
                element.keyword in _REMOVE_KEYWORDS
                or vr in _FREE_TEXT_VRS
                or (0x5000 <= tag.group <= 0x50FF or 0x6000 <= tag.group <= 0x60FF)
            ):
                del ds[tag]
                report.elements_removed += 1
            elif vr == "SQ":
                for item in element.value:
                    self._scrub(item, report)
            elif vr == "PN":
                element.value = ""
                report.elements_modified += 1
            elif vr == "DA":
                # Safe Harbor permits the year only.
                element.value = _map_values(element.value, lambda v: f"{v[:4]}0101" if v else v)
                report.elements_modified += 1
            elif vr == "DT":
                element.value = _map_values(
                    element.value, lambda v: f"{v[:4]}0101000000" if v else v
                )
                report.elements_modified += 1
            elif vr == "TM":
                element.value = _map_values(element.value, lambda v: "000000" if v else v)
                report.elements_modified += 1
            elif vr == "UI":
                before = element.value
                element.value = _map_values(
                    before,
                    lambda v: v if not v or v.startswith(_DICOM_UID_ROOT) else self.remap_uid(v),
                )
                if element.value != before:
                    report.uids_remapped += 1

    @staticmethod
    def _collect_warnings(ds: Dataset, report: DeidReport) -> None:
        burnt_in = str(ds.get("BurnedInAnnotation", "") or "").upper()
        if burnt_in == "YES":
            report.warnings.append(
                "BurnedInAnnotation is YES: pixel data contains burnt-in text that was not removed"
            )
        elif burnt_in != "NO":
            report.warnings.append(
                "BurnedInAnnotation is not declared: pixel data was not checked for burnt-in PHI"
            )


def _map_values(value, transform):
    """Apply ``transform`` to a single- or multi-valued element value."""
    if value is None:
        return value
    if isinstance(value, (list, tuple)) or type(value).__name__ == "MultiValue":
        return [transform(str(item)) for item in value]
    return transform(str(value))


def _cap_age(age: str) -> str:
    """Aggregate ages over 89 into a single '90 or older' category."""
    if len(age) == 4 and age[:3].isdigit() and age[3].upper() == "Y":
        if int(age[:3]) > _SAFE_HARBOR_MAX_AGE:
            return "090Y"
    return age
