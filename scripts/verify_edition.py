#!/usr/bin/env python3
"""Fail-closed gate: refuse to deliver an edition whose date or freshness is wrong.

This runs after the audio is produced and before delivery. It re-derives the
edition identity from the system clock (never from the model) and checks the
artifacts against it, so a model that ignored Step 0 produces a loud failure
instead of a silently mis-dated podcast.

Usage:
  verify_edition.py --edition tech --audio <mp3> --script <md> [--sources <md>]
  verify_edition.py --edition politics --dir <edition_dir> --cron-job <id>   # infer artifacts

It also checks that metadata.json / caption.txt carry the exact provider/model_id
of this run (resolved by resolve_model.py from OpenClaw's run record), so a vague
"GPT-6" or a guessed suffix can never be delivered.

Exit 0 = safe to deliver. Exit 1 = do not deliver, report via the failure path.
Stdlib only, plus optional ffprobe for the duration check.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date

from edition_env import add_identity_args, resolve
from resolve_model import UNRESOLVED, from_cli, from_sqlite


# Sanity ceiling on the delivered file. At 96 kbps mono a ~4 min edition is ~3 MB;
# 50 MB is ~15x headroom yet catches a runaway mix long before it matters.
# Added after the 2026-07-28 incident (59 GiB MP3 filled the root filesystem).
MAX_AUDIO_BYTES = 50 * 1024 * 1024

# How far into the script the broadcast date must appear.
OPENING_CHARS = 200

MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December")
MONTH_YEAR_RE = re.compile(rf"\b({MONTHS})\s+((?:19|20)\d{{2}})\b", re.IGNORECASE)
CN_FULL_DATE_RE = re.compile(r"((?:19|20)\d{2})年(\d{1,2})月(\d{1,2})日")
ISO_DATE_RE = re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b")


class Report:
    def __init__(self):
        self.failures = []
        self.warnings = []
        self.passes = []

    def fail(self, check, detail):
        self.failures.append((check, detail))

    def warn(self, check, detail):
        self.warnings.append((check, detail))

    def ok(self, check, detail=""):
        self.passes.append((check, detail))

    def render(self):
        lines = []
        for check, detail in self.passes:
            lines.append(f"  PASS  {check}" + (f" — {detail}" if detail else ""))
        for check, detail in self.warnings:
            lines.append(f"  WARN  {check} — {detail}")
        for check, detail in self.failures:
            lines.append(f"  FAIL  {check} — {detail}")
        return "\n".join(lines)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def check_audio(rep, audio, env):
    if not os.path.isfile(audio):
        rep.fail("audio.exists", f"missing: {audio}")
        return
    size = os.path.getsize(audio)
    if size == 0:
        rep.fail("audio.nonempty", f"zero bytes: {audio}")
        return
    if size > MAX_AUDIO_BYTES:
        rep.fail("audio.size",
                 f"{size / 1048576:.1f} MiB exceeds {MAX_AUDIO_BYTES // 1048576} MiB cap — "
                 "mix ran away; do NOT deliver, re-run via scripts/mix_bgm.sh (SKILL.md 6)")
        return
    rep.ok("audio.size", f"{size / 1048576:.1f} MiB")

    actual = os.path.basename(audio)
    if actual != env["EDITION_BASENAME"]:
        rep.fail("audio.filename",
                 f"expected {env['EDITION_BASENAME']!r}, got {actual!r} "
                 "(filename must come from edition_env.py, not from memory)")
    else:
        rep.ok("audio.filename", actual)

    check_duration(rep, audio, env)


def check_duration(rep, audio, env):
    lo, hi = float(env["EDITION_DURATION_MIN_S"]), float(env["EDITION_DURATION_MAX_S"])
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", audio],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        rep.warn("audio.duration", f"skipped ({exc.__class__.__name__}); ffprobe unavailable")
        return

    try:
        secs = float(out.stdout.strip())
    except ValueError:
        rep.warn("audio.duration", f"skipped; ffprobe returned {out.stdout.strip()!r}")
        return

    pretty = f"{int(secs // 60)}分{int(secs % 60)}秒"
    if not lo <= secs <= hi:
        rep.fail("audio.duration",
                 f"{pretty} outside {int(lo)}-{int(hi)}s (edition config); regenerate per SKILL.md 5.5")
    else:
        rep.ok("audio.duration", pretty)


def check_script(rep, script_path, env):
    if not os.path.isfile(script_path):
        rep.fail("script.exists", f"missing: {script_path}")
        return

    text = read_text(script_path)
    opening = text[:OPENING_CHARS]
    want_cn = env["EDITION_DATE_CN"]

    if want_cn not in opening:
        found = CN_FULL_DATE_RE.findall(opening)
        got = "、".join(f"{y}年{m}月{d}日" for y, m, d in found) or "none"
        rep.fail("script.spoken_date",
                 f"opening must state {want_cn!r}; found {got} "
                 "(the wrong year would be read aloud in the audio)")
        return

    rep.ok("script.spoken_date", want_cn)

    # Correct date present, but a second conflicting one would still mislead.
    for y, m, d in CN_FULL_DATE_RE.findall(opening):
        if f"{int(y)}年{int(m)}月{int(d)}日" != want_cn:
            rep.fail("script.stray_date",
                     f"opening also contains {y}年{m}月{d}日, expected only {want_cn}")


def check_sources(rep, sources_path, env):
    if not os.path.isfile(sources_path):
        rep.warn("sources.exists", f"missing: {sources_path}; skipped source checks")
        return

    text = read_text(sources_path)
    year = env["EDITION_YEAR"]

    # Catches the observed failure: search queries pinned to the wrong year,
    # e.g. "AI breakthrough news July 2025".
    bad_years = {y for _, y in MONTH_YEAR_RE.findall(text) if y != year}
    if bad_years:
        rep.fail("sources.query_year",
                 f"month+year references to {', '.join(sorted(bad_years))} but edition year is {year}; "
                 "search queries must not hardcode a year from memory")
    else:
        rep.ok("sources.query_year", f"all month+year references are {year}")

    check_source_freshness(rep, text, env)
    check_item_count(rep, text, env)


ITEM_RE = re.compile(r"^\s*(\d{1,2})[.、)]\s+\S", re.MULTILINE)


def check_item_count(rep, text, env):
    want = int(env["EDITION_ITEM_COUNT"])
    got = len({int(n) for n in ITEM_RE.findall(text)})
    if got == want:
        rep.ok("sources.item_count", f"{got} items")
    elif 0 < got < want:
        rep.warn("sources.item_count",
                 f"{got} items, edition wants {want}; allowed only if fresh news ran out — say so in anomalies")
    else:
        rep.warn("sources.item_count", f"found {got} numbered items, edition wants {want}")


def check_source_freshness(rep, text, env):
    cutoff = date.fromisoformat(env["EDITION_MIN_SOURCE_DATE"])
    edition_day = date.fromisoformat(env["EDITION_DATE"])

    dates = []
    for y, m, d in ISO_DATE_RE.findall(text):
        try:
            dates.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    # The edition's own header date is not a source date.
    dates = [d for d in dates if d != edition_day]

    if not dates:
        rep.warn("sources.freshness", "no ISO source dates found; cannot verify freshness")
        return

    stale = sorted({d for d in dates if d < cutoff})
    if len(stale) == len(set(dates)):
        rep.fail("sources.freshness",
                 f"every dated source is older than {cutoff} "
                 f"(oldest {min(dates)}); use web_search date_after={env['EDITION_SEARCH_AFTER']}")
    elif stale:
        rep.warn("sources.freshness",
                 f"{len(stale)} source(s) older than {cutoff}: "
                 + ", ".join(str(d) for d in stale[:5]))
    else:
        rep.ok("sources.freshness", f"all sources on or after {cutoff}")


# provider/model_id, e.g. openai/gpt-6.1-sol or minimax-portal/MiniMax-M3
MODEL_REF_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.:-]+$")


def resolve_expected_model(args):
    if not (args.cron_job or args.session_key):
        return None
    cron = args.cron_job[5:] if args.cron_job and args.cron_job.startswith("cron:") else args.cron_job
    res = from_sqlite(args.agent, cron, args.session_key) or from_cli(args.agent, cron, args.session_key)
    return res["models"] if res else None


def check_model(rep, base, args):
    meta_path = args.metadata_file or os.path.join(base, "metadata.json")
    if not os.path.isfile(meta_path):
        rep.fail("metadata.exists", f"missing: {meta_path} (SKILL.md step 7 requires it)")
        return
    try:
        meta = json.loads(read_text(meta_path))
        got = str(meta.get("model_used", "")).strip()
    except ValueError as exc:
        rep.fail("metadata.json", f"unparseable {meta_path}: {exc}")
        return

    expected = resolve_expected_model(args)
    if expected:
        want = expected[-1]
        if want not in got:
            rep.fail("metadata.model_used",
                     f"run record says {want!r}, metadata has {got!r}; copy $MODEL_USED from "
                     "resolve_model.py verbatim")
        elif len(expected) > 1 and not ("fallback" in got.lower() or "回退" in got):
            rep.fail("metadata.model_used",
                     f"run failed over ({' -> '.join(expected)}) but metadata does not say fallback")
        else:
            rep.ok("metadata.model_used", got)
    elif got.startswith(UNRESOLVED[:3]):
        rep.warn("metadata.model_used", f"{got} — pass --cron-job so the run record can be checked")
    elif not MODEL_REF_RE.match(got.split()[0] if got else ""):
        rep.fail("metadata.model_used",
                 f"{got!r} is not a full provider/model_id (e.g. openai/gpt-6.1-sol); "
                 "family names like GPT-6 are not allowed")
    else:
        rep.warn("metadata.model_used", f"{got} — format OK but not cross-checked (no --cron-job)")

    cap_path = args.caption_file or os.path.join(base, "caption.txt")
    if not os.path.isfile(cap_path):
        rep.warn("caption.model_used", f"missing: {cap_path}; cannot check the Telegram caption")
        return
    caption = read_text(cap_path)
    needle = expected[-1] if expected else got
    if needle and needle in caption:
        rep.ok("caption.model_used", needle)
    else:
        rep.fail("caption.model_used", f"caption does not contain {needle!r}; it must match metadata")


def check_params(rep, base, args, env):
    """metadata must report the configured voice/speed/export/BGM, or explain why not."""
    meta_path = args.metadata_file or os.path.join(base, "metadata.json")
    try:
        meta = json.loads(read_text(meta_path))
    except (OSError, ValueError):
        return  # check_model already reported it
    explained = bool(str(meta.get("anomalies", "")).strip()) and "normal" not in str(meta.get("anomalies", "")).lower()
    want = {
        "tts_voice": env["EDITION_TTS_VOICE"],
        "speed": env["EDITION_TTS_SPEED_LABEL"],
        "export": env["EDITION_EXPORT_LABEL"],
        "has_bgm": "是" if env["EDITION_BGM_ENABLED"] == "1" else "否",
    }
    for key, val in want.items():
        got = str(meta.get(key, "")).strip()
        if got.startswith(val):
            rep.ok(f"metadata.{key}", got)
        elif explained:
            rep.warn(f"metadata.{key}", f"{got!r} differs from config {val!r} (anomalies present — check they explain it)")
        else:
            rep.fail(f"metadata.{key}", f"{got!r} differs from config {val!r} and anomalies do not explain it")


def parse_args(argv):
    p = argparse.ArgumentParser(description="Verify an edition before delivery. Pass the SAME "
                                "--edition/--config/--topic/--time/--date as edition_env.py.")
    add_identity_args(p)
    p.add_argument("--dir", help="Edition dir; infers audio/script/sources when not given explicitly.")
    p.add_argument("--audio", help="Final MP3 path.")
    p.add_argument("--script", dest="script_file", help="Chinese script file.")
    p.add_argument("--sources", dest="sources_file", help="Sources markdown file.")
    p.add_argument("--metadata", dest="metadata_file", help="metadata.json (default: <dir>/metadata.json).")
    p.add_argument("--caption", dest="caption_file", help="caption.txt (default: <dir>/caption.txt).")
    p.add_argument("--cron-job", help="Cron job id from the prompt's '[cron:<id> ...]' line.")
    p.add_argument("--session-key", help="Explicit OpenClaw session key (manual runs).")
    p.add_argument("--agent", help="Agent id for the model lookup (default: search all).")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    _cfg, env = resolve(args)

    base = args.dir or env["EDITION_DIR"]
    audio = args.audio or os.path.join(base, env["EDITION_BASENAME"])
    script_file = args.script_file or os.path.join(base, "script.md")
    sources_file = args.sources_file or os.path.join(base, "sources.md")

    rep = Report()
    check_audio(rep, audio, env)
    check_script(rep, script_file, env)
    check_sources(rep, sources_file, env)
    check_model(rep, base, args)
    check_params(rep, base, args, env)

    print(f"edition: {env['EDITION_SLUG']}  ({env['EDITION_DATE_CN']} {env['EDITION_WEEKDAY_CN']}, "
          f"{env['EDITION_TZ']})")
    print(rep.render())

    if rep.failures:
        print(f"\nRESULT: FAIL ({len(rep.failures)} blocking) — do NOT deliver; "
              "report via SKILL.md step 9.")
        return 1
    print(f"\nRESULT: OK ({len(rep.warnings)} warning(s)) — safe to deliver.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
