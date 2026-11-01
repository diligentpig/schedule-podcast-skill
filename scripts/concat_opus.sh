#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   concat_opus.sh <audio_dir> <out_dir>
# Expects part01.opus... in <audio_dir>
# Produces: a concatenated intermediate + voice_only.mp3 in <out_dir>

AUDIO_DIR="${1:?audio_dir}"
OUT_DIR="${2:?out_dir}"
mkdir -p "$OUT_DIR"

LIST="$OUT_DIR/list.txt"
: > "$LIST"

# Natural sort
for f in $(ls -1 "$AUDIO_DIR"/part*.opus 2>/dev/null | sort -V); do
  bn=$(basename "$f")
  printf "file '%s'\n" "$bn" >> "$LIST"
  # copy parts into OUT_DIR to simplify concat paths
  cp -f "$f" "$OUT_DIR/$bn"
done

if ! grep -q "^file 'part" "$LIST"; then
  echo "No part*.opus found in $AUDIO_DIR" >&2
  exit 2
fi

FIRST_PART=$(ls -1 "$OUT_DIR"/part*.opus | sort -V | head -1)
FORMAT_NAME=$(ffprobe -v error -show_entries format=format_name -of default=nk=1:nw=1 "$FIRST_PART")

case "$FORMAT_NAME" in
  mp3)
    INTERMEDIATE="$OUT_DIR/full.mp3"
    ;;
  ogg|opus)
    INTERMEDIATE="$OUT_DIR/full.opus"
    ;;
  *)
    echo "Unsupported part format: $FORMAT_NAME" >&2
    exit 3
    ;;
esac

ffmpeg -hide_banner -loglevel error -f concat -safe 0 -i "$LIST" -c copy "$INTERMEDIATE"
ffmpeg -hide_banner -loglevel error -i "$INTERMEDIATE" -c:a libmp3lame -b:a 96k -ar 24000 -ac 1 "$OUT_DIR/voice_only.mp3"

echo "OK: $INTERMEDIATE"
echo "OK: $OUT_DIR/voice_only.mp3"
