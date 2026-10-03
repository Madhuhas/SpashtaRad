"""DICOM pixel data -> normalised 2D float image.

All pipelines consume the same representation: a single-channel float32 array
in [0, 1] where higher values are brighter (MONOCHROME2 convention).
"""

from __future__ import annotations

import numpy as np
from pydicom.dataset import Dataset
from pydicom.pixels import apply_modality_lut, apply_voi_lut

from spashta_ai.errors import UnsupportedImageError
from spashta_ai.types import NormalizedRoi


def to_display_array(ds: Dataset) -> np.ndarray:
    """Decode a DICOM dataset into a 2D float32 image in [0, 1].

    Handles rescale slope/intercept, VOI LUT / window, MONOCHROME1 inversion,
    colour images (converted to luminance) and multi-frame objects (the middle
    frame is used).
    """
    if "PixelData" not in ds:
        raise UnsupportedImageError("DICOM object has no PixelData")

    try:
        pixels = ds.pixel_array
    except Exception as exc:  # decoder plugins raise a variety of types
        raise UnsupportedImageError(f"Unable to decode pixel data: {exc}") from exc

    samples = int(getattr(ds, "SamplesPerPixel", 1))
    frames = int(getattr(ds, "NumberOfFrames", 1) or 1)

    if frames > 1:
        pixels = pixels[frames // 2]
    if samples > 1:
        # Colour secondary captures / ultrasound: collapse to luminance.
        image = pixels.astype(np.float32).mean(axis=-1)
        return _min_max(image)
    if pixels.ndim != 2:
        raise UnsupportedImageError(f"Unexpected pixel array shape {pixels.shape}")

    image = apply_modality_lut(pixels, ds)
    has_voi = "VOILUTSequence" in ds or ("WindowCenter" in ds and "WindowWidth" in ds)
    if has_voi:
        image = _min_max(np.asarray(apply_voi_lut(image, ds), dtype=np.float32))
    else:
        image = _robust_normalise(np.asarray(image, dtype=np.float32))

    if str(getattr(ds, "PhotometricInterpretation", "MONOCHROME2")).upper() == "MONOCHROME1":
        image = 1.0 - image
    return image


def center_crop_square(image: np.ndarray) -> tuple[np.ndarray, NormalizedRoi]:
    """Crop the largest centred square; also return the crop as a normalised ROI."""
    rows, cols = image.shape
    size = min(rows, cols)
    y0 = (rows - size) // 2
    x0 = (cols - size) // 2
    roi = (x0 / cols, y0 / rows, (x0 + size) / cols, (y0 + size) / rows)
    return image[y0 : y0 + size, x0 : x0 + size], roi


def to_uint8(image: np.ndarray) -> np.ndarray:
    return np.clip(image * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _min_max(image: np.ndarray) -> np.ndarray:
    lo, hi = float(image.min()), float(image.max())
    if hi <= lo:
        return np.zeros_like(image, dtype=np.float32)
    return ((image - lo) / (hi - lo)).astype(np.float32)


def _robust_normalise(image: np.ndarray) -> np.ndarray:
    """Percentile window: resistant to collimation borders and burnt-in markers."""
    lo, hi = np.percentile(image, [0.5, 99.5])
    if hi <= lo:
        return _min_max(image)
    return np.clip((image - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)
