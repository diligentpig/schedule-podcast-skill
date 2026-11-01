#!/usr/bin/env python3
"""Resolve the canonical edition identity for one podcast run.

The edition date must never be authored by the model. This script derives every
form the pipeline needs (filename stamp, spoken Chinese date, search-query year,
freshness cutoff) from the system clock, so downstream steps only ever copy a
value instead of formatting or recalling one.

Usage:
  edition_env.py --edition tech
  edition_env.py --edition politics --out-root /path/to/workspace
  edition_env.py --topic special --time 1200 --format json
  edition_env.py --edition tech --date 2026-11-02          # manual re-run of a past edition

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

# Recurring editions and their scheduled local time.
PRESETS = {
    "tech": (7, 0),
    "politics": (23, 0),
}

WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


# A scheduled run belongs to slot T when it starts within [T - EARLY, T + SPILL].
# SPILL covers a late start or the 23:00 edition running past midnight; EARLY
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
      (The earlier "nearest slot" rule stamped a 19:30 tech re-run with
      tomorrow's date and a 10:30 politics re-run with yesterday's.)
    """
    slot = time(hour, minute)
    if on_date is not None:
        return datetime.combine(on_date, slot, tzinfo=now.tzinfo)
    for d in (0, -1, 1):
        t = datetime.combine((now + timedelta(days=d)).date(), slot, tzinfo=now.tzinfo)
        if t - EARLY <= now <= t + SPILL:
            return t
    return datetime.combine(now.date(), slot, tzinfo=now.tzinfo)


def build_env(topic, hour, minute, tz, out_root, max_age_days, now=None, on_date=None):
    now = now or datetime.now(tz)
    dt = resolve_edition_dt(now, hour, minute, on_date)
    cutoff = (dt - timedelta(days=max_age_days)).date()

    slug = f"{dt:%Y%m%d}_{dt:%H%M}_{topic}"
    edition_dir = os.path.join(os.path.abspath(out_root), slug)

    return {
        "EDITION_TZ": str(tz),
        "EDITION_TOPIC": topic,
        "EDITION_DATE": f"{dt:%Y-%m-%d}",
        "EDITION_STAMP": f"{dt:%Y%m%d}",
        "EDITION_HHMM": f"{dt:%H%M}",
        "EDITION_YEAR": f"{dt:%Y}",
        "EDITION_MONTH_EN": calendar.month_name[dt.month],
        "EDITION_DATE_CN": f"{dt.year}年{dt.month}月{dt.day}日",
        "EDITION_WEEKDAY_CN": WEEKDAY_CN[dt.weekday()],
        "EDITION_SLUG": slug,
        "EDITION_BASENAME": f"{slug}.mp3",
        # Freshness knobs: pass to web_search(date_after=...) and use as the
        # hard cutoff when ranking candidates.
        "EDITION_MAX_AGE_DAYS": str(max_age_days),
        "EDITION_SEARCH_AFTER": f"{cutoff:%Y-%m-%d}",
        "EDITION_MIN_SOURCE_DATE": f"{cutoff:%Y-%m-%d}",
        # Paths are derived from --out-root (default: cwd), never hardcoded.
        "EDITION_OUT_ROOT": os.path.abspath(out_root),
        "EDITION_DIR": edition_dir,
        "EDITION_SCRIPT_FILE": os.path.join(edition_dir, "script.md"),
        "EDITION_SOURCES_FILE": os.path.join(edition_dir, "sources.md"),
        "EDITION_AUDIO_FILE": os.path.join(edition_dir, f"{slug}.mp3"),
        "EDITION_RESOLVED_AT": f"{now:%Y-%m-%d %H:%M:%S %Z}",
    }


def parse_args(argv):
    p = argparse.ArgumentParser(description="Emit the canonical edition identity for a podcast run.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--edition", choices=sorted(PRESETS), help="Recurring edition preset.")
    g.add_argument("--topic", help="Arbitrary topic slug for one-off runs (requires --time).")
    p.add_argument("--time", dest="hhmm", help="Edition time label HHMM; overrides the preset.")
    p.add_argument("--date", dest="on_date",
                   help="Edition date YYYY-MM-DD; only for a manual re-run of a specific edition.")
    p.add_argument("--tz", default="Asia/Shanghai", help="IANA timezone (default: Asia/Shanghai).")
    p.add_argument("--out-root", default=".", help="Root dir for edition output (default: cwd).")
    p.add_argument("--max-age-days", type=int, default=2,
                   help="Sources older than EDITION_DATE minus this are rejected (default: 2).")
    p.add_argument("--now", help="ISO-8601 override for the current time (testing only).")
    p.add_argument("--format", choices=("sh", "json", "env"), default="sh",
                   help="sh: eval-able exports (default). env: bare KEY=VALUE. json: object.")
    return p.parse_args(argv)


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        sys.exit(f"error: --date must be YYYY-MM-DD, got {value!r}")


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    topic = args.edition or args.topic
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", topic):
        sys.exit(f"error: topic must be lowercase alphanumeric/_/-, got {topic!r}")

    if args.hhmm:
        if not re.fullmatch(r"([01]\d|2[0-3])[0-5]\d", args.hhmm):
            sys.exit(f"error: --time must be HHMM, got {args.hhmm!r}")
        hour, minute = int(args.hhmm[:2]), int(args.hhmm[2:])
    elif args.edition:
        hour, minute = PRESETS[args.edition]
    else:
        sys.exit("error: --topic requires --time")

    if args.max_age_days < 0:
        sys.exit("error: --max-age-days must be >= 0")

    try:
        tz = ZoneInfo(args.tz)
    except ZoneInfoNotFoundError:
        sys.exit(f"error: unknown timezone {args.tz!r}")

    now = None
    if args.now:
        try:
            now = datetime.fromisoformat(args.now)
        except ValueError:
            sys.exit(f"error: --now must be ISO-8601, got {args.now!r}")
        now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)

    on_date = parse_date(args.on_date)

    env = build_env(topic, hour, minute, tz, args.out_root, args.max_age_days, now, on_date)

    if args.format == "json":
        print(json.dumps(env, ensure_ascii=False, indent=2))
    else:
        prefix = "export " if args.format == "sh" else ""
        for k, v in env.items():
            print(f"{prefix}{k}={shlex.quote(v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
