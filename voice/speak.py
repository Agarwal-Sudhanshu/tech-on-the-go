"""Brief script -> narration in the cloned voice (local, free).

    speak.py --brief out/demo/brief.json --outdir out/demo [--ref voice/ref.wav]

Writes <outdir>/voice-raw.wav and <outdir>/timings.json (when each segment and
each spoken passage starts/ends, used to sync slides and captions).

This file is the only place that knows about the voice provider, so it can be
swapped (e.g. for ElevenLabs) without touching the rest of the pipeline.
"""
import argparse
import json
import os
import re
import time

import torch
import torchaudio as ta

# Checkpoints were saved on GPU; force them onto CPU when loading.
_orig_load = torch.load
torch.load = lambda *a, **k: _orig_load(*a, **{**k, "map_location": torch.device("cpu")})

from chatterbox.tts import ChatterboxTTS  # noqa: E402

CHUNK_PAUSE = 0.25    # between passages inside a segment
SEGMENT_PAUSE = 0.75  # between stories


def chunks(text, limit=280):
    """Split into sentence groups; the model is most stable on short passages."""
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        if cur and len(cur) + len(sent) + 1 > limit:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--ref", default="voice/ref.wav")
    ap.add_argument("--exaggeration", type=float, default=0.4)
    ap.add_argument("--cfg", type=float, default=0.75)
    args = ap.parse_args()

    brief = json.load(open(args.brief, encoding="utf-8"))
    model = ChatterboxTTS.from_pretrained(device="cpu")
    sr = model.sr

    parts, timings, cursor, t0 = [], [], 0.0, time.time()

    def add(wav):
        nonlocal cursor
        parts.append(wav)
        cursor += wav.shape[1] / sr

    segs = brief["segments"]
    for si, seg in enumerate(segs):
        seg_t = {"index": si, "start": cursor, "chunks": []}
        pieces = chunks(seg["text"])
        for ci, piece in enumerate(pieces):
            print(f"[segment {si + 1}/{len(segs)} part {ci + 1}/{len(pieces)}] {piece[:60]}...", flush=True)
            wav = model.generate(
                piece,
                audio_prompt_path=args.ref,
                exaggeration=args.exaggeration,
                cfg_weight=args.cfg,
            )
            start = cursor
            add(wav)
            seg_t["chunks"].append({"text": piece, "start": start, "end": cursor})
            if ci < len(pieces) - 1:
                add(torch.zeros(1, int(sr * CHUNK_PAUSE)))
        seg_t["speech_end"] = cursor
        add(torch.zeros(1, int(sr * SEGMENT_PAUSE)))
        seg_t["end"] = cursor
        timings.append(seg_t)

    os.makedirs(args.outdir, exist_ok=True)
    ta.save(os.path.join(args.outdir, "voice-raw.wav"), torch.cat(parts, dim=1), sr)
    json.dump({"duration": cursor, "segments": timings},
              open(os.path.join(args.outdir, "timings.json"), "w"), indent=1)
    print(f"done: {cursor:.0f}s of audio in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
