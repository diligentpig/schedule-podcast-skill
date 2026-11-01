# schedule-podcast-skill

A reusable OpenClaw skill (`podcast-pipeline`) that turns the day's news into a short Chinese
audio podcast (Top-N items, a few minutes) and delivers it as one Telegram message: MP3 + caption.

Every podcast is an **edition** described by a JSON config. Two ship today:

| Edition | Time | Content |
|---|---|---|
| `tech` | 07:00 | Tech Top10 (AI, big tech, security, chips, robotics) |
| `politics` | 23:00 | Politics Top10 (China + world) |

Adding a new podcast = adding `editions/<id>.json` with only the values that differ from
`editions/defaults.json`, plus a cron job that invokes the skill with that edition.

## What a run does

1. resolve the edition (date, filename and all parameters) — `edition_env.py`
2. collect candidates with web search, drop stale items, rank Top-N
3. write a Chinese spoken script
4. segmented TTS — `tts_edge.py`
5. concat to voice-only MP3 — `concat_parts.sh`
6. mix background music — `mix_bgm.sh`
7. resolve the exact model used and write metadata + caption — `resolve_model.py`
8. fail-closed pre-delivery gate — `verify_edition.py`
9. deliver via Telegram; report any failure via Telegram too

See `SKILL.md` for the full operational contract.

## Configuration

Merge order, later wins (objects merge recursively, lists/scalars replace):

1. `editions/defaults.json` — shared defaults, equal to the current production setup
2. `editions/local.json` — host-specific values, git-ignored (copy `local.example.json`)
3. `editions/<id>.json` — the edition, or any file passed with `--config`

Configurable: topic description, schedule time, timezone, title, opening line, item count,
candidate pool, script length, duration window, search language / query count / freshness /
max source age / preferred sources / query hints, TTS engine / voice / rate / segment size /
fallback, BGM on/off / path / volume / download helper, export bitrate / sample rate /
channels, delivery retry and Dropbox upload.

```bash
python3 scripts/edition_config.py --list                 # available editions
python3 scripts/edition_config.py --edition tech         # merged config
python3 scripts/edition_env.py --edition tech            # this run's $EDITION_* values
python3 scripts/edition_env.py --topic special --time 1530 --title 特别播客   # one-off, defaults only
```

The Telegram chatId is **not** in the configs (this repo is public); the caller supplies it.

## Callers

- OpenClaw cron jobs: pass the edition id, the chatId and an `--out-root`; the workflow lives
  only in `SKILL.md`, so do not duplicate logic in the cron prompt.
- Delivery is done by the running agent itself; relaying through `main` or another agent is not
  allowed (enforced by config validation, `delivery.send_via` must be `self`).
- Manual re-run of a past edition: `--date YYYY-MM-DD`.

## Why the scripts exist

Each script replaces something the model used to author and got wrong:

| Was model-authored | Failure seen | Now |
|---|---|---|
| edition date / filename | wrong year in filename and narration | `edition_env.py` |
| item count, voice, speed, length | drifted per run | `editions/*.json` → `$EDITION_*` |
| TTS code | rewritten every run | `tts_edge.py` |
| `model_used` metadata | vague `GPT-6`, or "unverified" | `resolve_model.py` |
| ffmpeg mix command | 59 GiB runaway MP3 filled the disk | `mix_bgm.sh` only, with three caps |
| "looks fine, send it" | — | `verify_edition.py`, exit ≠ 0 blocks delivery |

## Requirements

`python3` ≥ 3.9 (stdlib), `ffmpeg`, `ffprobe`, `edge-tts` (pip). OpenClaw tools `web_search`
and `message`. A Dropbox download helper for the BGM (path in `editions/local.json`).
`resolve_model.py` reads OpenClaw's run records (`$OPENCLAW_STATE_DIR`, default `~/.openclaw`).
