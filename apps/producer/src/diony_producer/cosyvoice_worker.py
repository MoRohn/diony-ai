"""Small isolated CosyVoice runner for the GPU host Python environment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt-audio", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "third_party" / "Matcha-TTS"))

    import torch
    import torchaudio
    from cosyvoice.cli.cosyvoice import CosyVoice2
    from cosyvoice.utils.file_utils import load_wav

    voice = CosyVoice2(args.model)
    prompt = load_wav(args.prompt_audio, 16000)
    chunks = [
        row["tts_speech"]
        for row in voice.inference_cross_lingual(args.text, prompt)
        if row.get("tts_speech") is not None
    ]
    if not chunks:
        raise RuntimeError("CosyVoice returned no speech")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(str(output), torch.concat(chunks, dim=1), voice.sample_rate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
