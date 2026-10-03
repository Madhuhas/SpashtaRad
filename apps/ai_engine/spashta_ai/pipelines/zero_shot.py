"""BiomedCLIP zero-shot inference.

Serves two purposes:

* anatomy classification when the DICOM header does not say what was imaged;
* anomaly screening for anatomy without a dedicated supervised model.

Scores are contrastive similarities between the image and a positive/negative
prompt pair. They rank hypotheses but are NOT calibrated probabilities, and
every finding is flagged accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from spashta_ai.pipelines.base import Pipeline
from spashta_ai.preprocessing import to_uint8
from spashta_ai.types import AnatomyRegion, Finding, PipelineOutput, RoutingDecision

BIOMEDCLIP_ID = "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224"
_CONTEXT_LENGTH = 256


@dataclass(frozen=True)
class Hypothesis:
    label: str
    positive_prompt: str
    negative_prompt: str


# Canonical body part -> prompt used for anatomy classification.
ANATOMY_PROMPTS: dict[str, str] = {
    "CHEST": "a chest X-ray",
    "HAND": "an X-ray of the hand",
    "WRIST": "an X-ray of the wrist",
    "ELBOW": "an X-ray of the elbow",
    "SHOULDER": "an X-ray of the shoulder",
    "KNEE": "an X-ray of the knee",
    "ANKLE": "an X-ray of the ankle",
    "FOOT": "an X-ray of the foot",
    "PELVIS": "an X-ray of the pelvis",
    "ABDOMEN": "an abdominal X-ray",
    "SPINE": "an X-ray of the spine",
    "SKULL": "an X-ray of the skull",
}

_MSK_HYPOTHESES = (
    Hypothesis("Fracture", "a radiograph showing a bone fracture", "a radiograph showing intact bones with no fracture"),
    Hypothesis("Dislocation", "a radiograph showing a joint dislocation", "a radiograph showing normal joint alignment"),
    Hypothesis("Bone lesion", "a radiograph showing a lytic or sclerotic bone lesion", "a radiograph showing normal bone texture"),
    Hypothesis("Degenerative change", "a radiograph showing osteoarthritis with joint space narrowing", "a radiograph showing a normal joint space"),
)  # fmt: skip

_GENERAL_HYPOTHESES: dict[str, tuple[Hypothesis, ...]] = {
    "ABDOMEN": (
        Hypothesis("Bowel obstruction", "an abdominal radiograph showing dilated bowel loops from obstruction", "an abdominal radiograph with a normal bowel gas pattern"),
        Hypothesis("Pneumoperitoneum", "an abdominal radiograph showing free intraperitoneal air", "an abdominal radiograph with no free air"),
        Hypothesis("Urinary calculus", "an abdominal radiograph showing a renal or ureteric calculus", "an abdominal radiograph with no calcifications"),
    ),
    "SPINE": (
        Hypothesis("Vertebral compression fracture", "a spine radiograph showing a vertebral compression fracture", "a spine radiograph showing normal vertebral body heights"),
        Hypothesis("Degenerative disc disease", "a spine radiograph showing disc space narrowing and osteophytes", "a spine radiograph showing preserved disc spaces"),
        Hypothesis("Scoliosis", "a spine radiograph showing scoliosis", "a spine radiograph showing normal spinal alignment"),
    ),
    "SKULL": (
        Hypothesis("Skull fracture", "a skull radiograph showing a skull fracture", "a skull radiograph showing an intact skull"),
        Hypothesis("Intracranial lesion", "a head image showing an intracranial mass or hemorrhage", "a head image showing normal intracranial appearance"),
    ),
}  # fmt: skip

_FALLBACK_HYPOTHESES = (
    Hypothesis("Abnormality", "a medical image showing an abnormal finding", "a normal medical image with no abnormality"),
)  # fmt: skip

# Body-part values that share a hypothesis set.
_GENERAL_ALIASES = {
    "KUB": "ABDOMEN", "HEAD": "SKULL", "BRAIN": "SKULL",
    "CSPINE": "SPINE", "TSPINE": "SPINE", "LSPINE": "SPINE", "SSPINE": "SPINE",
    "TLSPINE": "SPINE", "CERVICALSPINE": "SPINE", "THORACICSPINE": "SPINE",
    "LUMBARSPINE": "SPINE",
}  # fmt: skip


def hypotheses_for(routing: RoutingDecision) -> tuple[Hypothesis, ...]:
    if routing.region is AnatomyRegion.MSK:
        return _MSK_HYPOTHESES
    body_part = routing.body_part or ""
    key = _GENERAL_ALIASES.get(body_part, body_part)
    return _GENERAL_HYPOTHESES.get(key, _FALLBACK_HYPOTHESES)


class BiomedClip:
    """Thin wrapper around the open_clip BiomedCLIP checkpoint."""

    def __init__(self, device: str = "cpu") -> None:
        import open_clip
        import torch

        self._torch = torch
        self._device = torch.device(device)
        model, preprocess = open_clip.create_model_from_pretrained(BIOMEDCLIP_ID)
        self._model = model.to(self._device).eval()
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(BIOMEDCLIP_ID)
        self._text_cache: dict[tuple[str, ...], object] = {}

    def similarity(self, image: np.ndarray, prompts: tuple[str, ...]) -> np.ndarray:
        """Temperature-scaled cosine similarity between one image and each prompt."""
        torch = self._torch
        pil = Image.fromarray(to_uint8(image)).convert("RGB")
        pixels = self._preprocess(pil).unsqueeze(0).to(self._device)
        with torch.no_grad():
            image_features = self._model.encode_image(pixels)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            text_features = self._encode_text(prompts)
            logits = self._model.logit_scale.exp() * image_features @ text_features.T
        return logits[0].cpu().numpy()

    def classify_anatomy(self, image: np.ndarray) -> tuple[str, float]:
        labels = tuple(ANATOMY_PROMPTS)
        logits = self.similarity(image, tuple(ANATOMY_PROMPTS[label] for label in labels))
        probabilities = _softmax(logits)
        best = int(probabilities.argmax())
        return labels[best], float(probabilities[best])

    def _encode_text(self, prompts: tuple[str, ...]):
        cached = self._text_cache.get(prompts)
        if cached is None:
            tokens = self._tokenizer(list(prompts), context_length=_CONTEXT_LENGTH).to(self._device)
            features = self._model.encode_text(tokens)
            cached = features / features.norm(dim=-1, keepdim=True)
            self._text_cache[prompts] = cached
        return cached


class ZeroShotPipeline(Pipeline):
    name = "biomedclip-zero-shot"

    def __init__(self, clip: BiomedClip) -> None:
        self._clip = clip

    def predict(
        self,
        image: np.ndarray,
        routing: RoutingDecision,
        *,
        explain: bool,
        threshold: float,
        max_heatmaps: int,
    ) -> PipelineOutput:
        findings = []
        for hypothesis in hypotheses_for(routing):
            logits = self._clip.similarity(
                image, (hypothesis.positive_prompt, hypothesis.negative_prompt)
            )
            score = float(_softmax(logits)[0])
            findings.append(
                Finding(
                    label=hypothesis.label,
                    probability=score,
                    positive=score >= threshold,
                    calibrated=False,
                )
            )
        findings.sort(key=lambda f: f.probability, reverse=True)
        return PipelineOutput(
            model_name="BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
            model_version="open_clip",
            findings=findings,
            notes=[
                "Zero-shot similarity scores are uncalibrated and for triage only",
                "Heatmaps are not available for zero-shot findings",
            ],
        )


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = np.exp(logits - logits.max())
    return shifted / shifted.sum()
