#!/usr/bin/env python3
"""Resolve the canonical identity and parameters for one podcast run.

The edition date must never be authored by the model, and neither should any
tunable (item count, voice, speed, length, BGM...). This script derives the date
from the system clock and every parameter from the edition config
(editions/*.json, see edition_config.py), so downstream steps only ever copy a
value instead of formatting, recalling or guessing one.

Usage:
  edition_env.py --edition tech
  edition_env.py --edition politics --out-root /path/to/workspace
  edition_env.py --config /path/to/my_edition.json
  edition_env.py --topic special --time 1200 --title 特别播客     # one-off, all defaults
  edition_env.py --edition tech --date 2026-11-02                 # manual re-run of a past edition

Stdlib only (zoneinfo needs Python >= 3.9). No host-specific paths.
"""

import argparse
import calendar
import json
import os
import re
import shlex
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from edition_config import ConfigError, list_editions, load_config

WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# A scheduled run belongs to slot T when it starts within [T - EARLY, T + SPILL].
# SPILL covers a late start or an evening edition running past midnight; EARLY
# covers a scheduler that fires a little ahead of time.
EARLY = timedelta(minutes=30)
SPILL = timedelta(hours=3)


def resolve_edition_dt(now, hour, minute, on_date=None):
    """Return the edition datetime for a run starting at `now`.

    - `on_date` given (manual re-run of a specific edition): that date at HH:MM.
    - `now` inside a slot's window (scheduled run, incl. 23:00 spilling past
      midnight): that slot.
    - Otherwise (manual off-schedule run): TODAY at HH:MM. Never tomorrow and
      never yesterday — the spoken date must match the day the news is from.
    """
    slot = time(hour, minute)
    if on_date is not None:
        return datetime.combine(on_date, slot, tzinfo=now.tzinfo)
    for d in (0, -1, 1):
        t = datetime.combine((now + timedelta(days=d)).date(), slot, tzinfo=now.tzinfo)
        if t - EARLY <= now <= t + SPILL:
            return t
    return datetime.combine(now.date(), slot, tzinfo=now.tzinfo)


def _range(v):
    return f"{v[0]}-{v[1]}" if isinstance(v, (list, tuple)) and len(v) == 2 else str(v)


def build_env(cfg, out_root, now=None, on_date=None):
    tz = ZoneInfo(cfg["tz"])
    now = (now or datetime.now(tz)).astimezone(tz)
    hour, minute = int(cfg["time"][:2]), int(cfg["time"][2:])
    dt = resolve_edition_dt(now, hour, minute, on_date)
    s, c, d, t, b, e, dl = (cfg[k] for k in ("search", "content", "duration", "tts", "bgm", "export", "delivery"))
    cutoff = (dt - timedelta(days=s["max_age_days"])).date()

    topic = cfg["id"]
    slug = f"{dt:%Y%m%d}_{dt:%H%M}_{topic}"
    out_root = os.path.abspath(out_root)
    edition_dir = os.path.join(out_root, slug)
    date_cn = f"{dt.year}年{dt.month}月{dt.day}日"
    weekday_cn = WEEKDAY_CN[dt.weekday()]
    fill = {"date_cn": date_cn, "weekday_cn": weekday_cn, "topic": topic, "hhmm": f"{dt:%H%M}"}
    title = cfg.get("title", "{topic}").format(**fill)
    fill["title"] = title

    return {
        # --- identity (from the clock) ---
        "EDITION_TZ": cfg["tz"],
        "EDITION_TOPIC": topic,
        "EDITION_TITLE": title,
        "EDITION_DATE": f"{dt:%Y-%m-%d}",
        "EDITION_STAMP": f"{dt:%Y%m%d}",
        "EDITION_HHMM": f"{dt:%H%M}",
        "EDITION_YEAR": f"{dt:%Y}",
        "EDITION_MONTH_EN": calendar.month_name[dt.month],
        "EDITION_DATE_CN": date_cn,
        "EDITION_WEEKDAY_CN": weekday_cn,
        "EDITION_OPENING": cfg.get("opening", "今天是{date_cn}。").format(**fill),
        "EDITION_SLUG": slug,
        "EDITION_BASENAME": f"{slug}.mp3",
        # --- content ---
        "EDITION_TOPIC_DESC": cfg.get("topic_desc", ""),
        "EDITION_ITEM_COUNT": str(c["item_count"]),
        "EDITION_CANDIDATE_POOL": _range(c["candidate_pool"]),
        "EDITION_SCRIPT_CHARS": _range(c["script_chars"]),
        "EDITION_PER_ITEM_CHARS": _range(c["per_item_chars"]),
        "EDITION_DURATION_TARGET_S": _range(d["target_s"]),
        "EDITION_DURATION_MIN_S": str(d["min_s"]),
        "EDITION_DURATION_MAX_S": str(d["max_s"]),
        # --- search ---
        "EDITION_SEARCH_LANG": s["query_language"],
        "EDITION_SEARCH_QUERIES": _range(s["query_count"]),
        "EDITION_SEARCH_COUNT": str(s["results_per_query"]),
        "EDITION_SEARCH_FRESHNESS": s["freshness"],
        "EDITION_SEARCH_DELAY_S": str(s["serial_delay_s"]),
        "EDITION_SEARCH_PROVIDER": s.get("provider_label", ""),
        "EDITION_PREFERRED_SOURCES": " | ".join(s.get("preferred_sources", [])),
        "EDITION_QUERY_HINTS": " | ".join(s.get("query_hints", [])),
        "EDITION_MAX_AGE_DAYS": str(s["max_age_days"]),
        "EDITION_SEARCH_AFTER": f"{cutoff:%Y-%m-%d}",
        "EDITION_MIN_SOURCE_DATE": f"{cutoff:%Y-%m-%d}",
        # --- voice ---
        "EDITION_TTS_ENGINE": t["engine"],
        "EDITION_TTS_VOICE": t["voice"],
        "EDITION_TTS_RATE": t["rate"],
        "EDITION_TTS_SPEED_LABEL": t.get("speed_label", t["rate"]),
        "EDITION_TTS_SEGMENT_MAX_CHARS": str(t["segment_max_chars"]),
        "EDITION_TTS_FALLBACK": t.get("fallback_engine", ""),
        # --- music / export ---
        "EDITION_BGM_ENABLED": "1" if b["enabled"] else "0",
        "EDITION_BGM_SOURCE": b.get("source", ""),
        "EDITION_BGM_PATH": b.get("path", ""),
        "EDITION_BGM_VOLUME": str(b["volume"]),
        "EDITION_BGM_DOWNLOAD_HELPER": b.get("download_helper", ""),
        "EDITION_EXPORT_BITRATE": e["bitrate"],
        "EDITION_EXPORT_SAMPLE_RATE": str(e["sample_rate"]),
        "EDITION_EXPORT_CHANNELS": str(e["channels"]),
        "EDITION_EXPORT_LABEL": f"{e['bitrate']}/{e['sample_rate'] // 1000}kHz/{'mono' if e['channels'] == 1 else 'stereo'}",
        # --- delivery ---
        "EDITION_DELIVERY_CHANNEL": dl["channel"],
        "EDITION_DELIVERY_RETRY_MIN": str(dl["retry_after_min"]),
        "EDITION_SAVE_TO_DROPBOX": "1" if dl["save_to_dropbox"] else "0",
        # --- paths (derived from --out-root, never hardcoded) ---
        "EDITION_OUT_ROOT": out_root,
        "EDITION_DIR": edition_dir,
        "EDITION_SCRIPT_FILE": os.path.join(edition_dir, "script.md"),
        "EDITION_SOURCES_FILE": os.path.join(edition_dir, "sources.md"),
        "EDITION_PARTS_DIR": os.path.join(edition_dir, "parts"),
        "EDITION_VOICE_FILE": os.path.join(edition_dir, "voice_only.mp3"),
        "EDITION_AUDIO_FILE": os.path.join(edition_dir, f"{slug}.mp3"),
        "EDITION_METADATA_FILE": os.path.join(edition_dir, "metadata.json"),
        "EDITION_CAPTION_FILE": os.path.join(edition_dir, "caption.txt"),
        "EDITION_LAST_OK_FILE": os.path.join(out_root, f"podcast_{topic}_last_ok.json"),
        "EDITION_RESOLVED_AT": f"{now:%Y-%m-%d %H:%M:%S %Z}",
    }


def add_identity_args(p):
    """Arguments shared with verify_edition.py, so both resolve the same edition."""
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--edition", help=f"Edition id from editions/ ({', '.join(list_editions()) or 'none'}).")
    g.add_argument("--config", help="Path to an edition JSON kept outside editions/.")
    g.add_argument("--topic", help="One-off topic slug using defaults only (requires --time).")
    p.add_argument("--time", dest="hhmm", help="Edition time HHMM; overrides the config.")
    p.add_argument("--title", help="Display title for a one-off --topic run.")
    p.add_argument("--date", dest="on_date",
                   help="Edition date YYYY-MM-DD; only for a manual re-run of a specific edition.")
    p.add_argument("--tz", help="IANA timezone; overrides the config (default Asia/Shanghai).")
    p.add_argument("--out-root", default=".", help="Root dir for edition output (default: cwd).")
    p.add_argument("--now", help="ISO-8601 override for the current time (testing only).")


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        sys.exit(f"error: --date must be YYYY-MM-DD, got {value!r}")


def resolve(args):
    """Return (cfg, env) for parsed identity args, exiting with a message on error."""
    over = {}
    if args.topic:
        if not args.hhmm:
            sys.exit("error: --topic requires --time")
        over["id"] = args.topic
        over["title"] = args.title or "{topic}播客"
    if args.hhmm:
        over["time"] = args.hhmm
    if args.tz:
        over["tz"] = args.tz
    try:
        cfg = load_config(args.edition, args.config, over)
        ZoneInfo(cfg["tz"])
    except ConfigError as exc:
        sys.exit(f"error: {exc}")
    except (ZoneInfoNotFoundError, ValueError):
        sys.exit(f"error: unknown timezone {cfg.get('tz')!r}")

    now = None
    if args.now:
        try:
            now = datetime.fromisoformat(args.now)
        except ValueError:
            sys.exit(f"error: --now must be ISO-8601, got {args.now!r}")
        tz = ZoneInfo(cfg["tz"])
        now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)

    return cfg, build_env(cfg, args.out_root, now, parse_date(args.on_date))


def main(argv=None):
    p = argparse.ArgumentParser(description="Emit the canonical identity and parameters for a podcast run.")
    add_identity_args(p)
    p.add_argument("--format", choices=("sh", "json", "env"), default="sh",
                   help="sh: eval-able exports (default). env: bare KEY=VALUE. json: object.")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    _cfg, env = resolve(args)

    if args.format == "json":
        print(json.dumps(env, ensure_ascii=False, indent=2))
    else:
        prefix = "export " if args.format == "sh" else ""
        for k, v in env.items():
            print(f"{prefix}{k}={shlex.quote(v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
