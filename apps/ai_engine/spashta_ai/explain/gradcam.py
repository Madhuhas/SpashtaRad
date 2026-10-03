"""Grad-CAM (Selvaraju et al., 2017).

The implementation is hook-free: the caller runs the network in two explicit
stages (feature extractor -> head) and passes the intermediate activations and
the scalar class score. This avoids the fragility of backward hooks around
in-place ReLUs, which DenseNet-style models use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import torch


def grad_cam(
    activations: torch.Tensor,
    score: torch.Tensor,
    *,
    retain_graph: bool = False,
) -> np.ndarray:
    """Compute a class activation map.

    Args:
        activations: Feature maps of shape ``(1, C, H, W)`` that are part of the
            autograd graph leading to ``score``.
        score: Scalar class score (ideally the pre-sigmoid logit).
        retain_graph: Keep the graph alive to explain further classes.

    Returns:
        ``(H, W)`` float32 map scaled to [0, 1]. All zeros if the class has no
        positive evidence in the feature maps.
    """
    import torch

    (gradients,) = torch.autograd.grad(score, activations, retain_graph=retain_graph)
    # Channel importance = global-average-pooled gradient.
    weights = gradients.mean(dim=(2, 3), keepdim=True)
    cam = torch.relu((weights * activations).sum(dim=1))[0].detach()
    peak = cam.max()
    if peak > 0:
        cam = cam / peak
    return cam.cpu().numpy().astype(np.float32)
