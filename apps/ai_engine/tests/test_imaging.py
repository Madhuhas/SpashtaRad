"""Preprocessing, overlay rendering and Secondary Capture export."""

import io

import numpy as np
import pydicom
import pytest
from PIL import Image
from pydicom.dataset import Dataset

from spashta_ai.errors import UnsupportedImageError
from spashta_ai.explain.overlay import blend_on_image, encode_png, render_overlay_rgba
from spashta_ai.explain.secondary_capture import build_secondary_capture, to_part10_bytes
from spashta_ai.preprocessing import center_crop_square, to_display_array


def test_display_array_is_normalised(dicom_factory):
    image = to_display_array(dicom_factory())
    assert image.shape == (96, 128)
    assert image.dtype == np.float32
    assert image.min() == 0.0 and image.max() == 1.0
    assert image[48, 64] > image[0, 0]  # bright square stays bright


def test_monochrome1_is_inverted(dicom_factory):
    image = to_display_array(dicom_factory(photometric="MONOCHROME1"))
    assert image[48, 64] < image[0, 0]


def test_window_is_applied(dicom_factory):
    image = to_display_array(dicom_factory(WindowCenter=3000, WindowWidth=100))
    assert image[0, 0] == 0.0 and image[48, 64] == 1.0


def test_missing_pixel_data_is_rejected():
    with pytest.raises(UnsupportedImageError):
        to_display_array(Dataset())


def test_center_crop_reports_roi():
    crop, roi = center_crop_square(np.zeros((100, 200), dtype=np.float32))
    assert crop.shape == (100, 100)
    assert roi == (0.25, 0.0, 0.75, 1.0)


def test_overlay_covers_full_frame_and_is_transparent_outside_roi():
    cam = np.zeros((7, 7), dtype=np.float32)
    cam[3, 3] = 1.0
    overlay = render_overlay_rgba(cam, (100, 200), (0.25, 0.0, 0.75, 1.0), max_side=200)

    assert overlay.shape == (100, 200, 4)
    assert overlay[:, :50, 3].max() == 0  # left of ROI
    assert overlay[:, 150:, 3].max() == 0  # right of ROI
    assert overlay[50, 100, 3] > 0  # activation peak is visible
    assert overlay[..., 3].max() <= round(0.6 * 255)

    decoded = Image.open(io.BytesIO(encode_png(overlay)))
    assert decoded.mode == "RGBA" and decoded.size == (200, 100)


def test_secondary_capture_round_trips(dicom_factory):
    source = dicom_factory()
    image = to_display_array(source)
    overlay = render_overlay_rgba(np.ones((7, 7), dtype=np.float32), image.shape)
    rgb = blend_on_image(image, overlay)

    sc = build_secondary_capture(
        source, rgb, finding_label="Cardiomegaly", model_name="test-model", model_version="1.0"
    )
    parsed = pydicom.dcmread(io.BytesIO(to_part10_bytes(sc)))

    assert parsed.SOPClassUID == "1.2.840.10008.5.1.4.1.1.7"
    assert parsed.StudyInstanceUID == source.StudyInstanceUID
    assert parsed.SeriesInstanceUID != source.SeriesInstanceUID
    assert parsed.PatientID == source.PatientID
    assert parsed.SourceImageSequence[0].ReferencedSOPInstanceUID == source.SOPInstanceUID
    assert parsed.BurnedInAnnotation == "YES"
    assert parsed.pixel_array.shape == (96, 128, 3)
