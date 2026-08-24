"""Small isolated CosyVoice runner for the GPU host Python environment."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path


def embedding_values(embedding: object) -> list[float]:
    """Normalize CAM++'s batched tensor into the flat contract vector."""
    if all(hasattr(embedding, name) for name in ("detach", "cpu", "reshape", "tolist")):
        embedding = embedding.detach().cpu().reshape(-1).tolist()  # type: ignore[union-attr]
    values: list[float] = []

    def collect(row: object) -> None:
        if isinstance(row, (list, tuple)):
            for item in row:
                collect(item)
            return
        values.append(float(row))  # type: ignore[arg-type]

    collect(embedding)
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt-audio")
    parser.add_argument("--text")
    parser.add_argument("--output")
    parser.add_argument("--embed-audio")
    parser.add_argument("--embedding-output")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()

    root = Path(args.root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "third_party" / "Matcha-TTS"))

    import torch
    import torchaudio
    from cosyvoice.cli.cosyvoice import CosyVoice2

    if args.seed is not None:
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(args.seed)
    voice = CosyVoice2(args.model)
    if args.embed_audio:
        if not args.embedding_output:
            parser.error("--embedding-output is required with --embed-audio")
        embedding = voice.frontend._extract_spk_embedding(args.embed_audio)
        destination = Path(args.embedding_output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({"embedding": embedding_values(embedding)}) + "\n",
            encoding="utf-8",
        )
        return 0
    if not args.prompt_audio or not args.text or not args.output:
        parser.error("--prompt-audio, --text, and --output are required for synthesis")
    chunks = [
        row["tts_speech"]
        for row in voice.inference_cross_lingual(args.text, args.prompt_audio)
        if row.get("tts_speech") is not None
    ]
    if not chunks:
        raise RuntimeError("CosyVoice returned no speech")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(str(output), torch.concat(chunks, dim=1), voice.sample_rate)
    if args.embedding_output:
        embedding = voice.frontend._extract_spk_embedding(str(output))
        destination = Path(args.embedding_output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({"embedding": embedding_values(embedding)}) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
