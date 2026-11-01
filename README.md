# schedule-podcast-skill

A reusable OpenClaw skill for generating a short Chinese news podcast (usually Top10) and delivering it as an MP3.

This repo packages the `podcast-pipeline` skill for scheduled runs such as:
- `07:00` morning tech news
- `23:00` evening politics news

## What this skill does

Given a topic, date/time label, and Telegram delivery target, the skill will:
1. collect candidate news with web search
2. dedupe and rank them into a Top10
3. write a Chinese spoken-style script
4. generate segmented TTS audio
5. concat to a voice-only MP3
6. mix background music by default
7. deliver one MP3 with caption metadata + Top10 summary

## Repo contents

- `SKILL.md` — the actual skill definition and workflow contract
- `scripts/edition_env.py` — Step 0: derive the edition date / filename / spoken date / freshness cutoff from the system clock
- `scripts/resolve_model.py` — Step 6.4: read the exact `provider/model_id` of the current run from OpenClaw's run record (detects fallback)
- `scripts/verify_edition.py` — Step 6.5: fail-closed pre-delivery gate (filename, spoken date, source freshness, duration, `model_used`)
- `scripts/concat_opus.sh` — concat `part*` TTS segments (Opus or MP3) and export `voice_only.mp3`
- `scripts/mix_bgm.sh` — mix voice + BGM into final MP3, with runaway-output guards

## Intended callers

This skill is meant for:
- OpenClaw scheduled jobs / cron wrappers
- other agents that need a reusable news-podcast generator
- manual one-off runs with explicit topic + delivery inputs

## Required inputs

Callers should decide and pass these inputs before invoking the skill:

- `topic`: typically `tech` or `politics`
- `chatId`: Telegram target chat id
- `edition_date` (optional): only for a manual re-run of a past edition (`--date YYYY-MM-DD`)

The edition date, time label and filename (`{YYYYMMDD}_{HHMM}_{topic}.mp3`) are **not** caller
inputs any more: `scripts/edition_env.py` derives them from the system clock, because a model
writing dates from memory produced podcasts stamped and narrated with the wrong year.
- `bgm_path` (optional): Dropbox App Folder path, default `/新闻播客 BGM.mp3`
- `save_to_dropbox` (optional): default `false`

## Expected output

The final delivery must be a **single Telegram message** containing:
- the MP3 attachment
- a caption with:
  - metadata block
  - Top10 title list

Typical file name examples:
- `20260511_0700_tech.mp3`
- `20260511_2300_politics.mp3`

## Scheduling contract

Important design rule:

- the **workflow lives only in `SKILL.md`**
- the **scheduler should only provide timing + invoke this skill + pass parameters**

So if you want to change generation logic, update `SKILL.md` instead of duplicating logic in cron or wrapper scripts.

## Runtime assumptions

This skill assumes the runtime environment has:
- `ffmpeg`
- `ffprobe`
- an available TTS path (Edge TTS by default; OpenAI TTS as fallback)
- OpenClaw tools such as `web_search`, `message`, and optionally `sessions_send`
- Dropbox download helper if BGM is fetched from Dropbox App Folder

Referenced helper outside this repo:
- `/home/wzf/.openclaw/workspace/scripts/dropbox_download.py`

## Notes for integrators

- Do not use generic filenames like `final.mp3`
- Use absolute paths in automation environments
- Do not silently skip failure reporting
- If BGM mixing fails, report anomaly and fall back to voice-only delivery only when allowed by the skill contract

## Why the scripts exist

Each script replaces something the model used to author and got wrong:

| Was model-authored | Failure seen | Now |
|---|---|---|
| edition date / filename | wrong year in filename and narration | `edition_env.py` |
| `model_used` metadata | vague `GPT-6`, or "unverified" | `resolve_model.py` |
| ffmpeg mix command | 59 GiB runaway MP3 filled the disk | `mix_bgm.sh` only, with three caps |
| "looks fine, send it" | — | `verify_edition.py`, exit ≠ 0 blocks delivery |

## Source skill name

OpenClaw skill name:
- `podcast-pipeline`

See `SKILL.md` for the full operational rules.
