#!/usr/bin/env bash
set -euo pipefail

# Mix voice MP3 + BGM MP3 with ducking.
# Usage:
#   mix_bgm.sh <voice_mp3> <bgm_mp3> <out_mp3> [bgm_volume] [bitrate] [sample_rate] [channels]
#   (defaults 0.12 96k 24000 1; pass $EDITION_BGM_VOLUME $EDITION_EXPORT_* from edition_env.py)
#
# NEVER hand-write the ffmpeg mix command instead of calling this script.
# On 2026-07-28 a hand-written variant produced a 59 GiB MP3 and filled the root
# filesystem. The trap: `-t` placed BEFORE an `-i` is an *input* option that binds
# to the NEXT input. In `-stream_loop -1 -i bgm.mp3 -t 190 -i voice.mp3` the -t
# truncates the voice, leaving the looped BGM unbounded; with `amix duration=first`
# resolving to that BGM and no `-t` on the output, ffmpeg writes until disk full.
#
# Three independent stops below, so no single mistake can run away again:
#   1. amix duration=shortest  — order-independent; the finite voice ends the mix
#   2. -t "$DUR"               — output-side hard duration cap
#   3. -fs "$MAX_BYTES"        — output-side hard byte cap (ffmpeg stops writing)

VOICE="${1:?voice_mp3}"
BGM="${2:?bgm_mp3}"
OUT="${3:?out_mp3}"
BGM_VOLUME="${4:-0.12}"
BITRATE="${5:-96k}"
SAMPLE_RATE="${6:-24000}"
CHANNELS="${7:-1}"

[[ "$BGM_VOLUME" =~ ^(0(\.[0-9]+)?|1(\.0+)?)$ ]] || { echo "ERROR: bgm_volume must be 0..1, got '$BGM_VOLUME'" >&2; exit 1; }
[[ "$BITRATE" =~ ^[0-9]+k$ ]] || { echo "ERROR: bitrate must look like 96k, got '$BITRATE'" >&2; exit 1; }
KBPS="${BITRATE%k}"

for f in "$VOICE" "$BGM"; do
  [ -s "$f" ] || { echo "ERROR: missing or empty input: $f" >&2; exit 1; }
done

DUR=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$VOICE" | awk '{printf "%.3f", $1}')

# Guard 0: a bad probe used to silently yield an empty/absurd -t. Fail loudly.
# 7200s ceiling is ~40x the ~5 min news edition and still far under any runaway.
python3 - <<PY || { echo "ERROR: implausible voice duration: '${DUR}'" >&2; exit 1; }
import sys
try:
    d = float("${DUR}")
except ValueError:
    sys.exit(1)
sys.exit(0 if 1.0 <= d <= 7200.0 else 1)
PY

# Guard 3 budget: CBR output is KBPS*1000/8 bytes/s (96k -> ~12 KB/s). Allow ~4x that
# plus 5 MB slack, so a correct render never trips it but a runaway dies within seconds.
MAX_BYTES=$(python3 -c "print(int(float('${DUR}') * ${KBPS} * 500) + 5*1024*1024)")

# Refuse to start if the filesystem cannot comfortably hold the result.
AVAIL_BYTES=$(df -Pk "$(dirname "$OUT")" | awk 'NR==2 {printf "%d", $4 * 1024}')
if [ "$AVAIL_BYTES" -lt $((MAX_BYTES * 4)) ]; then
  echo "ERROR: only $((AVAIL_BYTES / 1048576)) MiB free at $(dirname "$OUT"); need $((MAX_BYTES * 4 / 1048576)) MiB" >&2
  exit 1
fi

# Fade out starts 3s before end, but not <0
FADE_OUT=$(python3 -c "print(max(0.0, float('${DUR}') - 3.0))")

# Notes:
# - Loop BGM indefinitely, then let duration=shortest trim it to the voice
# - HP/LP to keep BGM out of vocal band
# - asplit voice so sidechain and mix can both consume it
# - amix normalize=0 to avoid killing BGM audibility

ffmpeg -hide_banner -loglevel error \
  -i "$VOICE" \
  -stream_loop -1 -i "$BGM" \
  -filter_complex "\
    [1:a]volume=${BGM_VOLUME},highpass=f=120,lowpass=f=8000,afade=t=in:st=0:d=1.5,afade=t=out:st=${FADE_OUT}:d=3[bgm];\
    [0:a]asplit=2[voice_sc][voice_mix];\
    [bgm][voice_sc]sidechaincompress=threshold=0.1:ratio=16:attack=5:release=300[bgmduck];\
    [bgmduck][voice_mix]amix=inputs=2:duration=shortest:dropout_transition=2:normalize=0,alimiter=limit=0.95[out]\
  " \
  -map "[out]" -t "$DUR" -fs "$MAX_BYTES" \
  -c:a libmp3lame -b:a "$BITRATE" -ar "$SAMPLE_RATE" -ac "$CHANNELS" "$OUT"

# Post-check: -fs stops the write but exits 0, so a truncated file would otherwise
# look like success. Compare against the voice duration we asked for.
OUT_DUR=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$OUT" | awk '{printf "%.3f", $1}')
python3 - <<PY || { echo "ERROR: output duration ${OUT_DUR}s does not match voice ${DUR}s — mix truncated or ran away" >&2; exit 1; }
import sys
want, got = float("${DUR}"), float("${OUT_DUR}" or 0)
sys.exit(0 if abs(got - want) <= max(1.0, want * 0.02) else 1)
PY

echo "OK: $OUT ($(du -h "$OUT" | cut -f1), ${OUT_DUR}s)"
