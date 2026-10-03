"""Shared fixtures: synthetic DICOM objects (no real patient data)."""

from __future__ import annotations

import numpy as np
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

_DX_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.1.1"


def make_dicom(
    *,
    rows: int = 96,
    cols: int = 128,
    body_part: str | None = "CHEST",
    modality: str | None = "DX",
    photometric: str = "MONOCHROME2",
    **extra: object,
) -> Dataset:
    """Build an in-memory 12-bit radiograph with a bright centred square."""
    pixels = np.full((rows, cols), 200, dtype=np.uint16)
    pixels[rows // 4 : 3 * rows // 4, cols // 4 : 3 * cols // 4] = 3000

    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = _DX_IMAGE_STORAGE
    meta.MediaStorageSOPInstanceUID = generate_uid()

    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = _DX_IMAGE_STORAGE
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientName = "TEST^SYNTHETIC"
    ds.PatientID = "SYN-0001"
    ds.PatientBirthDate = "19700101"
    ds.PatientSex = "O"
    if modality is not None:
        ds.Modality = modality
    if body_part is not None:
        ds.BodyPartExamined = body_part
    ds.Rows, ds.Columns = rows, cols
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = photometric
    ds.BitsAllocated = 16
    ds.BitsStored = 12
    ds.HighBit = 11
    ds.PixelRepresentation = 0
    ds.PixelData = pixels.tobytes()
    for keyword, value in extra.items():
        setattr(ds, keyword, value)
    return ds


@pytest.fixture
def dicom_factory():
    return make_dicom
