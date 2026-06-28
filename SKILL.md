---
name: podcast-pipeline
description: Generate a short Chinese audio podcast (≤5 min) from Top10 news: collect sources (web_search), dedupe+rank, write script, do segmented TTS (Opus), concat, export MP3, optionally mix Dropbox BGM with ducking/limiter, and deliver via Telegram (默认不保存到 Dropbox). Use for recurring OpenClaw podcast automation (07:00 tech news, 23:00 politics news) or one-off "make a podcast from these sources" tasks.
---

# Podcast pipeline (Top10 news → Chinese MP3)

## Inputs (decide first)
- **Edition datetime**: Asia/Shanghai date (YYYY-MM-DD) and optional time label (e.g. `2300`).
- **Topic**: e.g. `Tech Top10` / `Politics Top10 (CN+World)`.
- **Delivery**: Telegram chatId + file name rule.
- **Filename rule**: `{YYYYMMDD}_{HHMM}_{topic}.mp3`
  - 例如：`20260225_0700_tech.mp3` 或 `20260225_2300_politics.mp3`
  - topic 使用小写：tech / politics
- **Save to Dropbox**: 默认 **否**（只发Telegram）。只有用户显式要求时才上传到 Dropbox App Folder。
- **BGM**: 默认使用 `/新闻播客 BGM.mp3`（Dropbox App Folder 路径）。可覆盖。
- **BGM** (optional): 可指定其他 Dropbox App Folder 路径覆盖默认 BGM。
- **Web search constraints**: serial only, ≥2s spacing, 429 backoff.

## Output rules (Zhifang preference)
- **必须**在同一条消息中交付：MP3附件 + caption（先写元数据，再写 Top10 摘要列表）
- **元数据必须使用列表形式**（每行一项，建议以短横线或编号开头），避免长段落平铺。
- If **no anomalies**: 正常交付即可（元数据中标注 all normal）
- If **any anomaly** (429/401/network/retry/restart/fallback): 在元数据中注明异常情况及处理结果

## Model preference (fallback chain)
- **文稿生成**: 使用执行本任务的 agent 在 `openclaw.json` 中配置的模型（primary，过载时自动按 `fallbacks` 顺位切换）。本 skill **不指定**具体模型 id —— 模型由 openclaw.json 统一管理，避免文档与活配置漂移。
- **TTS**: Edge TTS **zh-CN-YunxiNeural** (男声) (default) → OpenAI TTS (fallback)
- **TTS 语速**: 1.2x (默认)

## Workflow
### 1) Collect candidate news (20-30)
- Use 3-6 **English** queries to cover the topic.
- Call `web_search` **serially** with ≥2s delay.
- Keep for each item: title, snippet, source site, URL.

### 2) Dedupe and rank → Top10
- Dedupe same event across sources; prefer official / Reuters / AP / gov / major outlets.
- Rank by impact + credibility + freshness.
- Internally store sources list (markdown file) for traceability.

### 3) Write Chinese script（目标约 3分30秒）
- **目标 950-1250 中文字**（目标成品时长约 3分10秒–3分50秒；避免出现 1–2 分钟的过短成品）
- 每条新闻至少 **90-130 字**，包含：标题 + 关键信息 + 背景/影响
- 避免只列标题，要有实质内容
- Avoid acronym soup; when needed, explain in Chinese once.
- Do **not** include prompts/instructions inside the script.

### 4) Segmented TTS (Opus)
- Split into 6-10 parts (30-60s each).
- Generate `part01.opus ... partNN.opus`.

### 5) Concat Opus → voice-only MP3
- Create `list.txt` and concat Opus (copy codec).
- Convert to MP3: `96k, 24kHz, mono`.
- See `scripts/concat_opus.sh`.

### 5.5) Duration gate（必须）
- 用 `ffprobe` 读取 voice-only / final MP3 的时长。
- **目标区间**：约 **190–230 秒**（≈3分10秒–3分50秒）。
- 若时长 < 180 秒：回到第 3 步，**扩写脚本**（每条多补 1–2 句背景/影响），重新 TTS → concat → mix，再交付。
- 若时长 > 260 秒：回到第 3 步，适当压缩每条，重新生成后再交付。

### 6) Mix BGM（默认必须启用）
- **默认必须混 BGM**（除非用户明确要求禁用）；Tech/Politics 两类定时播客都一样
- 默认 BGM 路径：`/新闻播客 BGM.mp3`（Dropbox App Folder）
- 每次运行时从 Dropbox 下载最新 BGM（不要依赖本地缓存）：见 `/home/wzf/.openclaw/workspace/scripts/dropbox_download.py`
- **必须循环播放填满全程**：将 BGM 循环拼接（loop）至与播客时长相同，确保 BGM 覆盖整个音频
- 若 BGM 下载/混音失败：在 anomalies 中明确标注，并退化为 voice-only 版本交付（仍需发 Telegram），不要静默省略
- **BGM 音量**：比人声低约 35-50%（BGM 增益建议设为 0.2~0.35），确保人声清晰可辨
- Mix 流程：loop BGM → HP/LP on BGM → sidechain ducking keyed by voice → `amix normalize=0` → fade in/out → limiter
- 如需禁用 BGM，在输入中明确指定
- See `scripts/mix_bgm.sh`.

### 7) Deliver
- Send final MP3 via `message` tool to Telegram with the **required filename** (NOT generic "final.mp3").
- **若当前 agent 无法直接发送到目标群**：
  - 使用 `sessions_send` 将以下内容发送给 `main` 代发：
    - MP3 绝对路径（`local_path`）
    - 文件名（`{YYYYMMDD}_{HHMM}_{topic}.mp3`）
    - 完整 caption（含 metadata + Top10 列表）
    - anomalies（如有）
- **交付前自检（必须）**：
  - `has_bgm` 必须为 是（除非用户明确禁用）
  - caption 必须同时包含：`metadata` 区块 + `Top10` 列表
  - 如果任一项不满足：不要发送"半成品"；应回到流程补齐（混 BGM / 补元数据），再发送
- caption **必须包含元数据**（metadata）与 **Top10 摘要列表**；不得只发 Top10 不发元数据
- **Filename must follow rule**: `{YYYYMMDD}_{HHMM}_{topic}.mp3`
- **必须使用绝对路径**（`/home/wzf/.openclaw/...`），**禁止使用 `~`**（cron 环境不会展开）
  - 例如：`20260227_0700_tech.mp3` 或 `20260227_2300_politics.mp3`
  - **禁止使用**：`final.mp3`, `output.mp3`, `podcast.mp3` 等无意义名称
- **默认不保存到 Dropbox**（除非用户显式要求）
- **禁止**把"Telegram 发送失败"作为触发条件去上传 Dropbox 兜底；失败就直接在群里报告失败原因与下一步
- **必须附带元数据**（同一条消息内，MP3文件作为附件，元数据放在caption中）：
  - 文稿模型：`model_used`（填实际生成本期文稿的模型，如 "MiniMax-M3 (primary)" 或 "gpt-5.4 (fallback)"，按运行时实际命中的模型如实标注）
  - Search API：`search_api`（如 Brave）
  - TTS 引擎：`tts_engine`（如 Edge TTS / OpenAI TTS）
  - TTS Voice：`tts_voice`（如 zh-CN-YunxiNeural）
  - 语速：`speed`（如 1.2x）
  - 是否混 BGM：`has_bgm`（是/否）+ BGM 路径
  - 导出参数：`export`（如 96k/24kHz/mono）
  - 成品时长：`duration`（如 3分42秒）
  - 产物本地路径：`local_path`
  - Dropbox 路径：`dropbox_path`（**默认: 不上传，留空**）
- **必须附带内容摘要**：Top10 新闻标题列表（**全部10条**，每条一行，不需要来源）

### 8) Log metadata
- 成功交付后，将上述元数据写入记录文件（如 `podcast_xxx_last_ok.json`），确保可追溯。

### 9) Failure feedback (必须)
- **任何失败都必须发送 Telegram 反馈**，不能静默退出。
- 失败消息格式：
  ```
  ❌ {日期}_{时间}_{topic} 播客生成失败
  
  失败阶段：{collect/dedupe/script/tts/concat/mix/deliver}
  失败原因：{具体错误信息}
  下一步建议：{如retry/人工介入等}
  ```
- 失败反馈必须发送到与成功交付相同的 Telegram chatId。
- 如果是 API rate limit 失败，错误信息必须包含 `FailoverError: ⚠️ API rate limit reached` 以便排查。

## Files & scripts
- `scripts/concat_opus.sh`: concat Opus + export voice-only MP3.
- `scripts/mix_bgm.sh`: produce final MP3 with BGM ducking.
- `/home/wzf/.openclaw/workspace/scripts/dropbox_download.py`: refresh token + download a Dropbox App Folder file.

## Guardrails
- Never auto-submit forms.
- Never paste secrets into chat.
- If Dropbox BGM path fails: treat as anomaly; attempt fallback only if user policy allows.
- **任何异常都必须发送 Telegram 反馈**（包括成功和失败），不允许静默。

## Scheduling contract (Important)
- **生成流程只记录在本 SKILL.md**；不要在定时任务里重复写流程细节。
- **定时任务只负责：时间 + 调用哪个 skill（podcast-pipeline）+ 必要的输入参数**（如 topic、日期/时间标签、chatId）。
- 若需要调整流程，**只改 SKILL.md**，不要改定时任务脚本/配置。
