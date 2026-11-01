#!/usr/bin/env python3
"""Load the merged configuration for one podcast edition.

Every tunable of the pipeline (topic, schedule time, item count, length, search,
voice, speed, BGM, export, delivery) lives in JSON under editions/, so a new
podcast is a new config file rather than a SKILL.md rewrite.

Merge order (later wins, dicts merge recursively, lists/scalars replace):
  1. editions/defaults.json   shared defaults = the current production setup
  2. editions/local.json      host-specific values, git-ignored (optional)
  3. editions/<id>.json       the edition itself (or --config <path>)

Usage:
  edition_config.py --edition tech            # print merged config as JSON
  edition_config.py --list                    # list available editions
  edition_config.py --config /path/x.json     # an edition kept outside the repo

Stdlib only.
"""

import argparse
import copy
import json
import os
import re
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITIONS_DIR = os.environ.get("PODCAST_EDITIONS_DIR") or os.path.join(SKILL_DIR, "editions")
RESERVED = {"defaults", "local", "local.example"}
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
HHMM_RE = re.compile(r"([01]\d|2[0-3])[0-5]\d")


class ConfigError(Exception):
    pass


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load(path, required):
    if not os.path.isfile(path):
        if required:
            raise ConfigError(f"config not found: {path}")
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}")
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be an object")
    return data


def list_editions(editions_dir=EDITIONS_DIR):
    if not os.path.isdir(editions_dir):
        return []
    return sorted(f[:-5] for f in os.listdir(editions_dir)
                  if f.endswith(".json") and f[:-5] not in RESERVED)


def load_config(edition=None, config_path=None, overrides=None, editions_dir=EDITIONS_DIR):
    """Return the merged config dict. `overrides` is applied last (ad-hoc runs)."""
    cfg = _load(os.path.join(editions_dir, "defaults.json"), required=True)
    cfg = deep_merge(cfg, _load(os.path.join(editions_dir, "local.json"), required=False))
    if config_path:
        cfg = deep_merge(cfg, _load(config_path, required=True))
    elif edition:
        if edition in RESERVED:
            raise ConfigError(f"{edition!r} is reserved, not an edition")
        path = os.path.join(editions_dir, f"{edition}.json")
        if not os.path.isfile(path):
            raise ConfigError(f"unknown edition {edition!r}; available: {', '.join(list_editions(editions_dir))}")
        cfg = deep_merge(cfg, _load(path, required=True))
        cfg.setdefault("id", edition)
    if overrides:
        cfg = deep_merge(cfg, overrides)
    validate(cfg)
    return cfg


def validate(cfg):
    def need(cond, msg):
        if not cond:
            raise ConfigError(msg)

    need(ID_RE.fullmatch(str(cfg.get("id", ""))), f"id must be lowercase alphanumeric/_/-, got {cfg.get('id')!r}")
    need(HHMM_RE.fullmatch(str(cfg.get("time", ""))), f"time must be HHMM, got {cfg.get('time')!r}")
    c, d, t, b, e = (cfg.get(k, {}) for k in ("content", "duration", "tts", "bgm", "export"))
    need(isinstance(c.get("item_count"), int) and c["item_count"] >= 1, "content.item_count must be an int >= 1")
    need(0 < d.get("min_s", 0) < d.get("max_s", 0), "duration needs 0 < min_s < max_s")
    need(isinstance(cfg.get("search", {}).get("max_age_days"), int) and cfg["search"]["max_age_days"] >= 0,
         "search.max_age_days must be an int >= 0")
    need(re.fullmatch(r"[+-]\d{1,3}%", str(t.get("rate", ""))), f"tts.rate must look like +20%, got {t.get('rate')!r}")
    need(t.get("voice"), "tts.voice is required")
    need(isinstance(b.get("volume", 0), (int, float)) and 0 <= b.get("volume", 0) <= 1, "bgm.volume must be 0..1")
    need(re.fullmatch(r"\d+k", str(e.get("bitrate", ""))), f"export.bitrate must look like 96k, got {e.get('bitrate')!r}")
    need(e.get("channels") in (1, 2), "export.channels must be 1 or 2")
    need(cfg.get("delivery", {}).get("send_via") == "self",
         "delivery.send_via must be 'self' (relaying through another agent is not allowed)")


def main(argv=None):
    p = argparse.ArgumentParser(description="Print the merged config of a podcast edition.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--edition")
    g.add_argument("--config")
    g.add_argument("--list", action="store_true")
    args = p.parse_args(argv)
    if args.list:
        print("\n".join(list_editions()))
        return 0
    try:
        cfg = load_config(args.edition, args.config)
    except ConfigError as exc:
        sys.exit(f"error: {exc}")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
