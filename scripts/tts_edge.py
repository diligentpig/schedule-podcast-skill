#!/usr/bin/env python3
"""Segmented Edge TTS for a podcast script: script.md -> parts/part01.mp3 ...

Replaces the per-run hand-written edge_tts snippet, so voice, speed and segment
size come from the edition config (EDITION_TTS_* from edition_env.py) instead of
whatever the model typed that day.

Usage:
  tts_edge.py --script "$EDITION_SCRIPT_FILE" --out-dir "$EDITION_PARTS_DIR" \
              --voice "$EDITION_TTS_VOICE" --rate "$EDITION_TTS_RATE" \
              --max-chars "$EDITION_TTS_SEGMENT_MAX_CHARS"

Writes partNN.txt (the exact text spoken) next to each partNN.mp3.
Exit 0 = all parts written. Exit 1 = a part failed after retries (use the
configured fallback engine, and record it in anomalies).

Requires the `edge_tts` Python package (pip install edge-tts).
"""

import argparse
import asyncio
import glob
import os
import re
import sys

RETRIES = 3

MD_STRIP = [
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE), ""),        # headings
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),                    # images
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),                # links -> text
    (re.compile(r"(\*\*|__|\*|`)"), ""),                          # emphasis / code
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),              # bullets
    (re.compile(r"^\s*>\s?", re.MULTILINE), ""),                  # quotes
    (re.compile(r"^\s*(---+|===+)\s*$", re.MULTILINE), ""),       # rules
]
SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?]*")


def clean(text):
    for rx, rep in MD_STRIP:
        text = rx.sub(rep, text)
    return text


def segment(text, max_chars):
    parts, cur = [], ""
    for para in clean(text).split("\n"):
        for s in SENTENCE_RE.findall(para):
            s = s.strip()
            if not s:
                continue
            if cur and len(cur) + len(s) > max_chars:
                parts.append(cur)
                cur = ""
            cur += s
        # paragraph boundary is a natural break when the segment is already sizeable
        if cur and len(cur) >= max_chars * 0.6:
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    return parts


async def synth(edge_tts, text, voice, rate, out):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            await edge_tts.Communicate(text, voice, rate=rate).save(out)
            if os.path.getsize(out) > 0:
                return
            last = RuntimeError("empty output")
        except Exception as exc:  # network / service errors
            last = exc
        await asyncio.sleep(2 * attempt)
    raise RuntimeError(f"{os.path.basename(out)} failed after {RETRIES} tries: {last}")


async def run(args, parts):
    import edge_tts
    for i, text in enumerate(parts, 1):
        base = os.path.join(args.out_dir, f"part{i:02d}")
        with open(base + ".txt", "w", encoding="utf-8") as fh:
            fh.write(text)
        await synth(edge_tts, text, args.voice, args.rate, base + ".mp3")
        print(f"part{i:02d}: {len(text)} chars", flush=True)


def main(argv=None):
    p = argparse.ArgumentParser(description="Segmented Edge TTS for a podcast script.")
    p.add_argument("--script", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--voice", required=True, help="e.g. zh-CN-YunxiNeural")
    p.add_argument("--rate", required=True, help="Edge rate, e.g. +20%% for 1.2x")
    p.add_argument("--max-chars", type=int, default=235)
    p.add_argument("--dry-run", action="store_true", help="Only print the segmentation.")
    args = p.parse_args(argv)

    if not re.fullmatch(r"[+-]\d{1,3}%", args.rate):
        sys.exit(f"error: --rate must look like +20%, got {args.rate!r}")
    with open(args.script, encoding="utf-8") as fh:
        parts = segment(fh.read(), args.max_chars)
    if not parts:
        sys.exit("error: script is empty after cleaning")

    print(f"segments: {len(parts)} {[len(x) for x in parts]}", flush=True)
    if args.dry_run:
        return 0

    try:
        import edge_tts  # noqa: F401
    except ImportError:
        sys.exit("error: edge_tts not installed (pip install edge-tts); use the fallback engine")

    os.makedirs(args.out_dir, exist_ok=True)
    for stale in glob.glob(os.path.join(args.out_dir, "part*.*")):
        os.remove(stale)  # never concat leftovers from an earlier attempt
    try:
        asyncio.run(run(args, parts))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(parts)} parts in {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
