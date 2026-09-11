"""Optional prompt-adherence evidence from a local CLIP encoder (Blueprint 24).

With ``DIONY_CLIP_MODEL`` naming a local ``openai/clip-vit-base-patch32``
style checkpoint directory (transformers format, no network), five sampled
frames of a negative are embedded, mean-pooled, and compared with the plan's
subject layer; the cosine similarity is recorded as ``prompt_adherence`` and
joins the attempt score. Without the model the field stays None.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from diony_producer.quality import decode_sampled_frames, sample_frame_indices

# ViT-B/32 cosine on a 512-d joint space: ≥0.30 reads as a strong match,
# 0.22–0.30 is judgement territory, below 0.22 the picture is not the prompt.
ADHERENCE_STRONG = 0.30
ADHERENCE_FLOOR = 0.22


def clip_model_path() -> Path | None:
    raw = os.environ.get("DIONY_CLIP_MODEL", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


@lru_cache(maxsize=1)
def _load(model_dir: str):
    import torch  # type: ignore[import-not-found]
    from transformers import CLIPModel, CLIPProcessor  # type: ignore[import-not-found]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CLIPModel.from_pretrained(model_dir, local_files_only=True).to(device).eval()
    processor = CLIPProcessor.from_pretrained(model_dir, local_files_only=True)
    return model, processor, device


def prompt_adherence(negative: Path, subject_text: str, *, frame_count: int) -> float | None:
    """Mean-pooled frame embedding vs subject text cosine, or None when unmeasurable."""
    model_dir = clip_model_path()
    text = " ".join(str(subject_text or "").split())
    if model_dir is None or not text or not Path(negative).is_file():
        return None
    try:
        import torch  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]

        frames = decode_sampled_frames(
            negative, sample_frame_indices(frame_count), width=448, height=252
        )
        if not frames:
            return None
        model, processor, device = _load(str(model_dir))
        images = [Image.fromarray(frame) for frame in frames]
        with torch.no_grad():
            image_inputs = processor(images=images, return_tensors="pt").to(device)
            image_embeds = model.get_image_features(**image_inputs)
            image_embeds = image_embeds / image_embeds.norm(dim=-1, keepdim=True)
            pooled = image_embeds.mean(dim=0)
            pooled = pooled / pooled.norm()
            text_inputs = processor(
                text=[text[:300]], return_tensors="pt", padding=True, truncation=True, max_length=77
            ).to(device)
            text_embeds = model.get_text_features(**text_inputs)[0]
            text_embeds = text_embeds / text_embeds.norm()
            score = float((pooled * text_embeds).sum().item())
    except Exception:
        return None
    return round(max(0.0, min(1.0, score)), 4)


__all__ = ["ADHERENCE_FLOOR", "ADHERENCE_STRONG", "clip_model_path", "prompt_adherence"]
