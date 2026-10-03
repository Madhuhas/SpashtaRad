"""Render class activation maps as viewer overlays."""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from spashta_ai.types import FULL_FRAME, NormalizedRoi

# Activations below this fraction of the peak are fully transparent, so the
# overlay highlights salient regions rather than tinting the whole image.
_ACTIVATION_FLOOR = 0.2


def render_overlay_rgba(
    cam: np.ndarray,
    image_shape: tuple[int, int],
    roi: NormalizedRoi = FULL_FRAME,
    *,
    max_side: int = 512,
    max_alpha: float = 0.6,
) -> np.ndarray:
    """Render a CAM as an RGBA image covering the *whole* source frame.

    The result has the source image's aspect ratio (longest side ``max_side``),
    so a viewer can stretch it over the image without knowing about the ROI
    the model actually looked at. Pixels outside the ROI are transparent.
    """
    rows, cols = image_shape
    scale = max_side / max(rows, cols)
    out_h, out_w = max(1, round(rows * scale)), max(1, round(cols * scale))

    x0, y0, x1, y1 = roi
    left, top = round(x0 * out_w), round(y0 * out_h)
    width = max(1, round((x1 - x0) * out_w))
    height = max(1, round((y1 - y0) * out_h))
    # Rounding can push the ROI one pixel past the canvas edge.
    width, height = min(width, out_w - left), min(height, out_h - top)

    cam = np.clip(cam.astype(np.float32), 0.0, 1.0)
    cam = cv2.resize(cam, (width, height), interpolation=cv2.INTER_CUBIC)
    cam = np.clip(cam, 0.0, 1.0)

    colour = cv2.applyColorMap((cam * 255).astype(np.uint8), cv2.COLORMAP_JET)
    colour = cv2.cvtColor(colour, cv2.COLOR_BGR2RGB)
    alpha = np.clip((cam - _ACTIVATION_FLOOR) / (1.0 - _ACTIVATION_FLOOR), 0.0, 1.0)

    overlay = np.zeros((out_h, out_w, 4), dtype=np.uint8)
    overlay[top : top + height, left : left + width, :3] = colour
    overlay[top : top + height, left : left + width, 3] = (alpha * max_alpha * 255).astype(np.uint8)
    return overlay


def encode_png(rgba: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def blend_on_image(
    image: np.ndarray, overlay_rgba: np.ndarray, *, max_side: int = 2048
) -> np.ndarray:
    """Alpha-blend an overlay onto a [0, 1] greyscale image -> RGB uint8.

    Used for DICOM Secondary Capture export, where the overlay must be burnt
    in because SC objects cannot carry an alpha layer.
    """
    rows, cols = image.shape
    scale = min(1.0, max_side / max(rows, cols))
    out_h, out_w = max(1, round(rows * scale)), max(1, round(cols * scale))

    base = cv2.resize(image.astype(np.float32), (out_w, out_h), interpolation=cv2.INTER_AREA)
    base_rgb = np.repeat((np.clip(base, 0, 1) * 255.0)[..., None], 3, axis=2)

    overlay = cv2.resize(overlay_rgba, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    alpha = overlay[..., 3:4].astype(np.float32) / 255.0
    blended = base_rgb * (1.0 - alpha) + overlay[..., :3].astype(np.float32) * alpha
    return np.clip(blended + 0.5, 0, 255).astype(np.uint8)
