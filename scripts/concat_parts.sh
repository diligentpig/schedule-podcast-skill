#!/usr/bin/env bash
set -euo pipefail

# Concatenate TTS segments into one voice-only MP3.
# Usage:
#   concat_parts.sh <parts_dir> <out_dir> [bitrate] [sample_rate] [channels]
#   (export params default to 96k 24000 1; pass $EDITION_EXPORT_* from edition_env.py)
# Expects part01.mp3 ... or part01.opus ... in <parts_dir> (one kind, not mixed).
# Produces: <out_dir>/voice_only.mp3

PARTS_DIR="${1:?parts_dir}"
OUT_DIR="${2:?out_dir}"
BITRATE="${3:-96k}"
SAMPLE_RATE="${4:-24000}"
CHANNELS="${5:-1}"
mkdir -p "$OUT_DIR"

EXT=""
for e in mp3 opus; do
  if ls "$PARTS_DIR"/part*."$e" >/dev/null 2>&1; then
    [ -z "$EXT" ] || { echo "ERROR: both part*.mp3 and part*.opus in $PARTS_DIR; remove the stale set" >&2; exit 2; }
    EXT="$e"
  fi
done
[ -n "$EXT" ] || { echo "No part*.mp3 or part*.opus found in $PARTS_DIR" >&2; exit 2; }

LIST="$OUT_DIR/list.txt"
: > "$LIST"
# Absolute paths in the list, so parts need not be copied next to it.
ls -1 "$PARTS_DIR"/part*."$EXT" | sort -V | while read -r f; do
  printf "file '%s'\n" "$(cd "$(dirname "$f")" && pwd)/$(basename "$f")" >> "$LIST"
done

# Edge TTS may emit MP3 even under a .opus name: detect the real container.
FIRST_PART=$(ls -1 "$PARTS_DIR"/part*."$EXT" | sort -V | head -1)
FORMAT_NAME=$(ffprobe -v error -show_entries format=format_name -of default=nk=1:nw=1 "$FIRST_PART")
case "$FORMAT_NAME" in
  mp3)       INTERMEDIATE="$OUT_DIR/full.mp3" ;;
  ogg|opus)  INTERMEDIATE="$OUT_DIR/full.opus" ;;
  *) echo "Unsupported part format: $FORMAT_NAME" >&2; exit 3 ;;
esac

ffmpeg -y -hide_banner -loglevel error -f concat -safe 0 -i "$LIST" -c copy "$INTERMEDIATE"
ffmpeg -y -hide_banner -loglevel error -i "$INTERMEDIATE" \
  -c:a libmp3lame -b:a "$BITRATE" -ar "$SAMPLE_RATE" -ac "$CHANNELS" "$OUT_DIR/voice_only.mp3"

echo "OK: $INTERMEDIATE ($(grep -c . "$LIST") parts)"
echo "OK: $OUT_DIR/voice_only.mp3"
