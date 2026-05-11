#!/usr/bin/env bash
set -euo pipefail

# Mix voice MP3 + BGM MP3 with ducking.
# Usage:
#   mix_bgm.sh <voice_mp3> <bgm_mp3> <out_mp3>

VOICE="${1:?voice_mp3}"
BGM="${2:?bgm_mp3}"
OUT="${3:?out_mp3}"

DUR=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$VOICE" | awk '{printf "%.3f", $1}')
# Fade out starts 3s before end, but not <0
FADE_OUT=$(python3 - <<PY
import math
D=float("$DUR")
print(max(0.0, D-3.0))
PY
)

# Notes:
# - Loop BGM indefinitely then trim to voice duration
# - HP/LP to keep BGM out of vocal band
# - asplit voice so sidechain and mix can both consume it
# - amix normalize=0 to avoid killing BGM audibility

ffmpeg -hide_banner -loglevel error \
  -i "$VOICE" \
  -stream_loop -1 -i "$BGM" \
  -filter_complex "\
    [1:a]volume=0.12,highpass=f=120,lowpass=f=8000,afade=t=in:st=0:d=1.5,afade=t=out:st=${FADE_OUT}:d=3[bgm];\
    [0:a]asplit=2[voice_sc][voice_mix];\
    [bgm][voice_sc]sidechaincompress=threshold=0.1:ratio=16:attack=5:release=300[bgmduck];\
    [bgmduck][voice_mix]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95[out]\
  " \
  -map "[out]" -t "$DUR" -c:a libmp3lame -b:a 96k -ar 24000 -ac 1 "$OUT"

echo "OK: $OUT"
