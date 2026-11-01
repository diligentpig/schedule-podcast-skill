---
name: podcast-pipeline
description: Generate a short Chinese news podcast (Top-N, a few minutes) for a configured edition — collect sources (web_search), dedupe+rank, write script, segmented TTS, concat, mix Dropbox BGM with ducking, verify, and deliver one MP3 + caption via Telegram. Every tunable (topic, time, item count, length, search, voice, speed, BGM, export) lives in editions/*.json. Use for recurring OpenClaw podcast automation (currently 07:00 tech, 23:00 politics), one-off podcast runs, or setting up a NEW scheduled podcast edition.
---

# Podcast pipeline (Top-N news → Chinese MP3)

## 先读：本 skill 的两条总原则
1. **参数来自配置，不来自你。** 主题、时间、条数、字数、时长、搜索、音色、语速、BGM、导出、发送规则，
   全部写在 `editions/*.json`，由 Step 0 的 `edition_env.py` 输出为 `$EDITION_*` 变量。
   你只**逐字复制**这些值；本文中出现的具体数字（如 10 条、1.2x）只是**当前默认值的举例**，以 `$EDITION_*` 为准。
2. **日期、文件名、模型型号由脚本给出，交付前由脚本校验。** 模型不得自行书写或推断这三样（都出过错）。

`$SKILL_DIR` = 本 SKILL.md 所在目录。下文所有脚本都用 `"$SKILL_DIR/scripts/..."` 调用。

## Editions（档期配置）
- 合并顺序（后者覆盖前者）：`editions/defaults.json`（公共默认 = 当前生产配置）
  → `editions/local.json`（本机专属，不入 git，如 Dropbox 下载脚本路径；模板见 `local.example.json`）
  → `editions/<id>.json`（该档期只写与默认不同的项）。
- 现有档期：`tech`（07:00 晨间科技）、`politics`（23:00 晚间时政）。列出：`python3 "$SKILL_DIR/scripts/edition_config.py" --list`；
  查看某档完整参数：`... edition_config.py --edition tech`。
- 可配置项一览（defaults.json 中均有当前值）：

  | 分组 | 键 | 含义 |
  |---|---|---|
  | 身份 | `id` `time` `tz` `title` `topic_desc` `opening` | 档期 id、HHMM、时区、caption 标题、选题范围、开场白模板（`{date_cn}` `{weekday_cn}` `{title}`） |
  | 内容 | `content.item_count` `candidate_pool` `script_chars` `per_item_chars` | 条数、候选池、全文字数、每条字数 |
  | 时长 | `duration.target_s` `min_s` `max_s` | 目标区间与硬上下限（闸门按 min/max 判） |
  | 搜索 | `search.query_language` `query_count` `results_per_query` `freshness` `max_age_days` `serial_delay_s` `preferred_sources` `query_hints` | 查询语言、查询数、每次条数、新鲜度、来源最老天数、调用间隔、优先来源、参考查询 |
  | 配音 | `tts.engine` `voice` `rate` `speed_label` `segment_max_chars` `segments` `fallback_engine` | 引擎、音色、Edge 语速（`+20%`=1.2x）、显示用语速、分段字数 |
  | 音乐 | `bgm.enabled` `source` `path` `volume` `download_helper` | 是否混 BGM、来源、路径、BGM 音量（0–1）、下载脚本 |
  | 导出 | `export.bitrate` `sample_rate` `channels` | MP3 参数 |
  | 发送 | `delivery.channel` `send_via` `retry_after_min` `save_to_dropbox` | 渠道、发送方式（只允许 `self`）、失败重试间隔、是否存 Dropbox |
- **Telegram chatId 不在配置里**（仓库公开）：由调用方（cron prompt）提供。

### ⛔ 新增或修改档期（新播客）时，必须先问用户
新建 `editions/<id>.json` 或改动现有档期的**选题 / 搜索方式**之前，不得自行决定后直接上线。必须先向用户确认：
1. **搜索来源**：这个主题的新闻从哪里来（查询语言、关键词方向、优先/排除的来源）——这一项必须当场确定；
2. 逐项过一遍上表所有可配置项，用户没提的说明"沿用默认值 X"，请用户确认或修改；
3. 播出时间与 Telegram 目标（写进 cron prompt，不进仓库）。
用户确认后再写配置文件，并用 `edition_env.py --edition <id>` 与一次 `tts_edge.py --dry-run` 验证配置合法。

## Inputs
- **Edition**：调用方给出档期 id（`tech` / `politics` / 新档期），或给 `--config <path>`，
  或一次性主题 `--topic <slug> --time HHMM --title <标题>`（全部用默认参数）。
- **Telegram chatId**：调用方给出。
- **手动补跑某一期**：才给日期（`--date YYYY-MM-DD`）。定时任务与"现在跑一次"都不给。
- 其余一切参数来自配置，**不接受**模型自行调整；需要改就改配置文件。

## Output rules（用户偏好）
- **必须**在同一条消息中交付：MP3 附件 + caption（先写元数据，再写 Top-N 摘要列表）
- **元数据必须使用列表形式**（每行一项，以短横线开头），避免长段落平铺。
- 无异常：元数据中标注 all normal。
- 有任何异常（429/401/网络/重试/重启/fallback/参数与配置不符）：在元数据 anomalies 中注明异常及处理结果。

## Model preference
- **文稿生成**：使用执行本任务的 agent 在 `openclaw.json` 中配置的模型（primary，过载时按 `fallbacks` 切换）。
  本 skill **不指定**模型 id；但**交付时必须如实标注实际模型**（Step 6.4）。
- **TTS**：`$EDITION_TTS_ENGINE`（默认 Edge TTS）→ 失败时 `$EDITION_TTS_FALLBACK`（默认 OpenAI TTS），fallback 须记入 anomalies。

## Workflow
### 0) Resolve the edition（必须，第一步，不可跳过）
- 执行（`--out-root` 为产物根目录，不传则用当前工作目录）：
  ```
  python3 "$SKILL_DIR/scripts/edition_env.py" --edition tech --out-root <workspace>/podcasts
  ```
- 输出本期全部 `$EDITION_*` 变量，节选：
  ```
  EDITION_TITLE='晨间科技播客'          EDITION_SLUG=20261102_0700_tech
  EDITION_DATE_CN='2026年11月2日'       EDITION_BASENAME=20261102_0700_tech.mp3
  EDITION_OPENING='今天是2026年11月2日，星期一。早上好，欢迎收听晨间科技新闻。'
  EDITION_ITEM_COUNT=10                 EDITION_SCRIPT_CHARS=950-1250
  EDITION_TTS_VOICE=zh-CN-YunxiNeural   EDITION_TTS_RATE=+20%
  EDITION_DIR=<out-root>/20261102_0700_tech   ...（完整列表以实际输出为准）
  ```
- ⛔ **本次运行中出现的任何日期、年份、文件名、参数值，只能逐字复制上面的值。**
  - 禁止凭记忆或推理写年份；禁止自己做日期格式转换；禁止从 prompt 里的 `Current time:` 一行手工换算。
- 日期规则：在档期前 30 分钟 ~ 后 3 小时内启动 → 归属该档（23:00 档在 00:20 运行仍归属前一天）；
  其余时间手动运行 → 归属**当天**。补跑过去某期才加 `--date`，之后 Step 6.5 带同一个 `--date`。
- **记下本次的 cron 任务 ID**：定时任务 prompt 第一行形如 `[cron:<任务ID> <任务名>] ...`，
  把 `<任务ID>` 原样记为 `$CRON_JOB_ID`（Step 6.4 / 6.5 要用）。手动运行没有该行，留空。
- 所有产物写进 `$EDITION_DIR`（各文件路径见 `$EDITION_*_FILE`）。

### 1) Collect candidates（`$EDITION_CANDIDATE_POOL` 条）
- 用 `$EDITION_SEARCH_QUERIES` 个 `$EDITION_SEARCH_LANG` 查询覆盖 `$EDITION_TOPIC_DESC`。
  `$EDITION_QUERY_HINTS` 是参考查询方向，可按当天新闻调整措辞，但不得偏离选题范围。
- `web_search` **串行**调用，间隔 ≥ `$EDITION_SEARCH_DELAY_S` 秒，遇 429 退避。
- **每次调用必须带**：`count: $EDITION_SEARCH_COUNT`、`freshness: "$EDITION_SEARCH_FRESHNESS"`；
  候选不足再放宽到 `date_after: $EDITION_SEARCH_AFTER`。
- ⛔ **查询词禁止硬编码年份/月份**；优先用 `latest` / `today` / `this week`，确需限定只能用 `$EDITION_MONTH_EN $EDITION_YEAR`。
- 每条记录：title、snippet、source site、URL、**published date**。

### 2) Dedupe and rank → Top `$EDITION_ITEM_COUNT`
- 同一事件跨来源去重；优先 `$EDITION_PREFERRED_SOURCES`。
- **硬性新鲜度闸门（先筛后排）**：发布日期早于 `$EDITION_MIN_SOURCE_DATE` 的条目**直接丢弃**。
  - 过滤后不足 `$EDITION_ITEM_COUNT` 条：先补搜（`date_after: $EDITION_SEARCH_AFTER`），仍不足则按实际条数出刊并在 anomalies 说明，**不用旧闻凑数**。
- 通过闸门后按 impact + credibility 排名。
- 写 `$EDITION_SOURCES_FILE`，每条编号（`1. 标题`），并写明 `发布日期：YYYY-MM-DD` 与 URL（供 Step 6.5 校验）。

### 3) Write Chinese script
- 写到 `$EDITION_SCRIPT_FILE`，纯口播文本（不要 Markdown 标题、列表符号、链接，不要包含提示词/指令）。
- **第一句必须逐字使用 `$EDITION_OPENING`**（含 `$EDITION_DATE_CN`，会被念出来）。
- 全文 `$EDITION_SCRIPT_CHARS` 字；每条 `$EDITION_PER_ITEM_CHARS` 字，含标题 + 关键信息 + 背景/影响，不要只列标题。
- 避免缩写堆砌；必要时用中文解释一次。

### 4) Segmented TTS
- 只能通过脚本（禁止手写 edge_tts 代码）：
  ```
  python3 "$SKILL_DIR/scripts/tts_edge.py" --script "$EDITION_SCRIPT_FILE" --out-dir "$EDITION_PARTS_DIR" \
    --voice "$EDITION_TTS_VOICE" --rate "$EDITION_TTS_RATE" --max-chars "$EDITION_TTS_SEGMENT_MAX_CHARS"
  ```
- 输出 `part01.mp3 …`（每段同名 `.txt` 为实际朗读文本）。exit 1 = 失败已重试 3 次 → 改用 `$EDITION_TTS_FALLBACK`，
  音色尽量接近，按同样命名写入 `$EDITION_PARTS_DIR`，并在 anomalies 与 metadata 的 `tts_engine`/`tts_voice` 如实记录。

### 5) Concat → voice-only MP3
```
bash "$SKILL_DIR/scripts/concat_parts.sh" "$EDITION_PARTS_DIR" "$EDITION_DIR" \
  "$EDITION_EXPORT_BITRATE" "$EDITION_EXPORT_SAMPLE_RATE" "$EDITION_EXPORT_CHANNELS"
```
产出 `$EDITION_VOICE_FILE`。

### 5.5) Duration gate（必须）
- `ffprobe` 读取 `$EDITION_VOICE_FILE` 时长；目标 `$EDITION_DURATION_TARGET_S` 秒。
- < `$EDITION_DURATION_MIN_S` 秒：回到 Step 3 **扩写**（每条补 1–2 句背景/影响），重新 TTS → concat。
- > `$EDITION_DURATION_MAX_S` 秒：回到 Step 3 适当压缩，重新生成。

### 6) Mix BGM（`$EDITION_BGM_ENABLED`=1 时必须）
- 每次运行重新下载 BGM（不依赖本地缓存）：
  `python3 "$EDITION_BGM_DOWNLOAD_HELPER" "$EDITION_BGM_PATH" "$EDITION_DIR/bgm.mp3"`
  （下载脚本是宿主相关的，路径来自 `editions/local.json`；为空则视为 BGM 失败。）
- 混音只能通过脚本：
  ```
  bash "$SKILL_DIR/scripts/mix_bgm.sh" "$EDITION_VOICE_FILE" "$EDITION_DIR/bgm.mp3" "$EDITION_AUDIO_FILE" \
    "$EDITION_BGM_VOLUME" "$EDITION_EXPORT_BITRATE" "$EDITION_EXPORT_SAMPLE_RATE" "$EDITION_EXPORT_CHANNELS"
  ```
  脚本内：BGM 循环填满全程 → HP/LP → 人声 sidechain ducking → `amix normalize=0` → 淡入淡出 → limiter。
- `$EDITION_BGM_ENABLED`=0：把 `$EDITION_VOICE_FILE` 复制为 `$EDITION_AUDIO_FILE`，`has_bgm` 记为 否。
- BGM 下载/混音失败：退化为 voice-only 交付（仍需发 Telegram），`has_bgm` 记为 否，并在 anomalies 明确标注，不要静默省略。

#### ⛔ 禁止手写 ffmpeg 混音
- **不得**在 exec 里自己拼 ffmpeg 混音命令，**不得**另写 `manual_mix.sh` / `make_podcast.py` 之类一次性脚本。
  参数不合适就**改配置**（音量、码率）或改 `mix_bgm.sh`（它在 git 里，可追溯），不要绕过。
- 原因（2026-07-28 真实事故）：手写命令 `-stream_loop -1 -i bgm.mp3 -t 190 -i voice_only.mp3`
  写出了 **59 GiB / 60 天时长**的 MP3，写满根分区。`-t` 写在 `-i` 前是**输入选项**，截断的是人声；
  无限循环的 BGM 没有上限，`amix duration=first` 恰好是 BGM，输出端又没 `-t`，于是一直写到磁盘满。
- `mix_bgm.sh` 有三道独立止损：`amix duration=shortest`、输出端 `-t`、输出端 `-fs` 字节上限；
  外加时长/磁盘余量预检与产出时长比对（`-fs` 截断时 ffmpeg 仍返回 0）。

### 6.4) Resolve the model & write metadata/caption（必须，在 6.5 之前）
- **`model_used` 不许由你填写，只能复制脚本输出**（模型无法可靠说出自己的完整型号）：
  ```
  python3 "$SKILL_DIR/scripts/resolve_model.py" --cron-job "$CRON_JOB_ID"    # 手动运行时省略 --cron-job
  ```
  输出 `MODEL_USED=openai/gpt-6.1-sol` 这类完整 `provider/model_id`；若 `MODEL_FALLBACK` 非空，
  `model_used` 写成 `$MODEL_USED（fallback，主模型 X 失败）`。仅当脚本 exit 2 时才可写
  「未核实（无法取得当次实际模型 ID）」，并在 anomalies 说明。
- 写 `$EDITION_METADATA_FILE`（JSON），字段与取值：

  | 字段 | 取值 |
  |---|---|
  | `edition` | `$EDITION_SLUG` |
  | `model_used` | 见上 |
  | `search_api` | `$EDITION_SEARCH_PROVIDER` |
  | `tts_engine` / `tts_voice` / `speed` | 实际值；正常时 = 配置的 engine / `$EDITION_TTS_VOICE` / `$EDITION_TTS_SPEED_LABEL` |
  | `has_bgm` / `bgm_path` | 是/否 + `$EDITION_BGM_PATH` |
  | `export` | `$EDITION_EXPORT_LABEL` |
  | `duration` | 成品实测，如 `3分42秒` |
  | `local_path` | `$EDITION_AUDIO_FILE` |
  | `dropbox_path` | 默认留空 |
  | `anomalies` | 无异常写 `all normal` |
- 写 `$EDITION_CAPTION_FILE`：**将要发送的完整 caption**（Step 7 原样发送它）。格式：
  ```
  $EDITION_TITLE｜$EDITION_DATE_CN

  metadata
  - model_used：…
  - …（上表每个字段一行）

  Top{条数}
  1. 标题
  …
  ```

### 6.5) Edition gate（必须，交付前最后一道机械校验）
```
python3 "$SKILL_DIR/scripts/verify_edition.py" --edition tech --out-root <同 Step 0> --cron-job "$CRON_JOB_ID"
```
- 档期参数（`--edition` / `--config` / `--topic --time` / `--date`）必须与 Step 0 **完全相同**；手动运行省略 `--cron-job`。
- 校验：文件名、开场日期、来源错年份、来源新鲜度、条数、成品时长（配置的 min/max）、文件大小、
  metadata 与 caption 的 `model_used` == 运行记录中的实际模型、metadata 的音色/语速/导出/BGM == 配置（不符且未在 anomalies 解释 → FAIL）。
- 新鲜度：**全部**来源过期 → FAIL；**部分**过期 → WARN，须回到 Step 2 换掉，实在无法替换才在 anomalies 说明。
- **exit 0 才允许交付。exit 1 = 禁止发送**：按 FAIL 项回到对应步骤修正后重跑；无法修正走 Step 9。
  **绝不允许跳过本步或忽略退出码。**

### 7) Deliver（Telegram）
- 用 `message` 工具**由本 agent 直接发送**：附件 `$EDITION_AUDIO_FILE`，文件名 `$EDITION_BASENAME`，
  caption = `$EDITION_CAPTION_FILE` 的内容（同一条消息）。
- ⛔ **在 OpenClaw 中禁止交给 `main` 或其他 agent 代发**（不得用 `sessions_send` 转交）。
  发送失败：等 `$EDITION_DELIVERY_RETRY_MIN` 分钟重试一次；仍失败走 Step 9。
- 必须用绝对路径，禁止 `~`（cron 环境不展开）；禁止 `final.mp3` / `output.mp3` / `podcast.mp3` 等无意义文件名。
- `$EDITION_SAVE_TO_DROPBOX`=0 时不上传 Dropbox；**禁止**把"Telegram 发送失败"当作上传 Dropbox 兜底的理由。

### 8) Log metadata
- 成功交付后，把 metadata 加上 `messageId`、`delivered: true` 写入 `$EDITION_LAST_OK_FILE`
  （每档一个固定文件，覆盖写），`model_used` 与 caption、`metadata.json` 一致。

### 9) Failure feedback（必须）
- **任何失败都必须发送 Telegram 反馈**（同一 chatId，同样由本 agent 直接发送），不能静默退出：
  ```
  ❌ $EDITION_SLUG 播客生成失败

  失败阶段：{collect/dedupe/script/tts/concat/mix/gate/deliver}
  失败原因：{具体错误信息；gate 失败时附 FAIL 行}
  下一步建议：{retry / 人工介入等}
  ```
- API rate limit 失败时，错误信息必须包含 `FailoverError: ⚠️ API rate limit reached`。

## Files & scripts
- `editions/defaults.json` / `<id>.json` / `local.json`：档期配置（见上）。
- `scripts/edition_config.py`：加载并校验合并后的配置（`--list` / `--edition`）。
- `scripts/edition_env.py`：**Step 0** — 由时钟 + 配置输出本期全部 `$EDITION_*`。
- `scripts/tts_edge.py`：**Step 4** — 分段 Edge TTS（音色/语速/分段来自配置，自动重试）。
- `scripts/concat_parts.sh`：**Step 5** — 拼接 `part*.mp3|opus` 并导出人声 MP3（`concat_opus.sh` 为兼容旧名）。
- `scripts/mix_bgm.sh`：**Step 6** — BGM 混音，带防失控止损。
- `scripts/resolve_model.py`：**Step 6.4** — 从 OpenClaw 运行记录（只读）取本次实际 `provider/model_id`，含 fallback 检测。
- `scripts/verify_edition.py`：**Step 6.5** — 交付前 fail-closed 校验；exit≠0 即禁止发送。

## Portability
- `scripts/` 不含宿主绝对路径：全部走参数或配置，`--out-root` 默认取当前工作目录。
- 依赖：`python3` ≥ 3.9（标准库）、`ffmpeg`、`ffprobe`、`edge-tts`（pip）。
- 宿主相关（换机器需适配）：`editions/local.json`（BGM 下载脚本）、Telegram 投递工具与 chatId、cron 配置、
  `resolve_model.py` 读取的 OpenClaw 运行记录（`$OPENCLAW_STATE_DIR`，默认 `~/.openclaw`；非 OpenClaw 环境返回 exit 2，闸门只给 WARN）。
- 目前发送规则按 OpenClaw 定义（禁止代发）；若改由其他软件 / 命令行调用，需另行修订 Step 7/9。

## Guardrails
- ⛔ **绝不凭记忆写日期/年份**，唯一来源是 Step 0。
- ⛔ **绝不凭记忆或自我认知写 `model_used`**，唯一来源是 Step 6.4。
- ⛔ **绝不在运行中自行改参数**（条数、时长、音色、语速、BGM 等），要改就改 `editions/*.json`。
- ⛔ **新档期 / 改选题与搜索方式，先问用户**（见 Editions 一节）。
- ⛔ **绝不跳过 Step 6.5，也不许忽略退出码。**
- ⛔ **OpenClaw 中不得交给 main 或其他 agent 代发。**
- Never auto-submit forms. Never paste secrets into chat.
- **任何结果都必须有 Telegram 反馈**（成功或失败），不允许静默。
