"""Export AI overlays as DICOM Secondary Capture objects.

The SC instance joins the source study (same StudyInstanceUID) as a new
series, so any PACS viewer can display the AI result next to the original.
"""

from __future__ import annotations

import datetime as dt
import io

import cv2
import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

# 2.25 is the UUID-derived OID arc; no organisational root registration needed.
_IMPLEMENTATION_CLASS_UID = generate_uid(prefix="2.25.", entropy_srcs=["spashtarad-ai-engine"])
_BANNER = "AI OVERLAY - NOT FOR DIAGNOSTIC USE"

# Patient and study level attributes copied verbatim from the source instance.
_COPIED_KEYWORDS = (
    "PatientName", "PatientID", "PatientBirthDate", "PatientSex",
    "StudyInstanceUID", "StudyDate", "StudyTime", "StudyID", "StudyDescription",
    "AccessionNumber", "ReferringPhysicianName",
)  # fmt: skip


def build_secondary_capture(
    source: Dataset,
    rgb: np.ndarray,
    *,
    finding_label: str,
    model_name: str,
    model_version: str,
) -> Dataset:
    """Wrap an RGB uint8 image ``(rows, cols, 3)`` as an SC instance of ``source``'s study."""
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("rgb must be a (rows, cols, 3) uint8 array")

    rgb = _burn_in_banner(rgb, f"{_BANNER} | {finding_label}")
    now = dt.datetime.now(dt.UTC)
    sop_instance_uid = generate_uid()

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = sop_instance_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = _IMPLEMENTATION_CLASS_UID

    ds = Dataset()
    ds.file_meta = meta
    for keyword in _COPIED_KEYWORDS:
        # Type 2 attributes must be present even when empty.
        setattr(ds, keyword, source.get(keyword, ""))

    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = sop_instance_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.Modality = "OT"
    ds.ConversionType = "WSD"  # workstation-generated
    ds.SeriesNumber = 999
    ds.InstanceNumber = 1
    ds.SeriesDescription = f"SpashtaRad AI: {finding_label}"[:64]
    ds.ImageType = ["DERIVED", "SECONDARY"]
    ds.DerivationDescription = (
        f"Grad-CAM overlay for '{finding_label}' ({model_name} {model_version})"
    )
    ds.Manufacturer = "SpashtaRad AI"
    ds.SoftwareVersions = model_version[:64]
    ds.ContentDate = now.strftime("%Y%m%d")
    ds.ContentTime = now.strftime("%H%M%S")
    ds.BurnedInAnnotation = "YES"

    if "SOPInstanceUID" in source and "SOPClassUID" in source:
        reference = Dataset()
        reference.ReferencedSOPClassUID = source.SOPClassUID
        reference.ReferencedSOPInstanceUID = source.SOPInstanceUID
        ds.SourceImageSequence = [reference]

    ds.SamplesPerPixel = 3
    ds.PhotometricInterpretation = "RGB"
    ds.PlanarConfiguration = 0
    ds.Rows, ds.Columns = int(rgb.shape[0]), int(rgb.shape[1])
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = np.ascontiguousarray(rgb).tobytes()
    return ds


def to_part10_bytes(ds: Dataset) -> bytes:
    """Serialise a dataset as a DICOM Part 10 file."""
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def _burn_in_banner(rgb: np.ndarray, text: str) -> np.ndarray:
    out = rgb.copy()
    scale = max(0.4, out.shape[1] / 1400.0)
    thickness = max(1, round(scale * 2))
    origin = (round(12 * scale), round(32 * scale))
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(out, text, origin, font, scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
    cv2.putText(out, text, origin, font, scale, (255, 220, 0), thickness, cv2.LINE_AA)
    return out
