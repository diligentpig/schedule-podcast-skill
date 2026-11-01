---
name: podcast-pipeline
description: Generate a short Chinese audio podcast (≤5 min) from Top10 news: collect sources (web_search), dedupe+rank, write script, do segmented TTS (Opus), concat, export MP3, optionally mix Dropbox BGM with ducking/limiter, and deliver via Telegram (默认不保存到 Dropbox). Use for recurring OpenClaw podcast automation (07:00 tech news, 23:00 politics news) or one-off "make a podcast from these sources" tasks.
---

# Podcast pipeline (Top10 news → Chinese MP3)

## Inputs (decide first)
- **Edition datetime**: ⛔ **不由你决定，也不许凭记忆写。** 必须在 Step 0 由 `scripts/edition_env.py` 从系统时钟算出。见下方 Step 0。
- **Topic**: e.g. `Tech Top10` / `Politics Top10 (CN+World)`。对应 `--edition tech` / `--edition politics`。
- **Delivery**: Telegram chatId + file name rule.
- **Filename rule**: 直接使用 Step 0 输出的 `$EDITION_BASENAME`（形如 `20260725_0700_tech.mp3`）。
  - **不要自己拼**日期字符串——拼错年份的事已经发生过一次。
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
### 0) Resolve the edition identity（必须，第一步，不可跳过）
- 执行（`$SKILL_DIR` = 本 SKILL.md 所在目录；`--out-root` 不传则用当前工作目录）：
  ```
  python3 "$SKILL_DIR/scripts/edition_env.py" --edition tech      # 或 --edition politics
  ```
  - **手动补跑某一期**（例如用户说"把 10 月 31 日的科技播客重做一次"）才加 `--date YYYY-MM-DD`；
    定时任务和"现在跑一次"都**不要**加。之后 Step 6.5 必须带同一个 `--date`。
- 输出形如：
  ```
  export EDITION_DATE=2026-07-25          export EDITION_STAMP=20260725
  export EDITION_HHMM=0700                export EDITION_YEAR=2026
  export EDITION_SLUG=20260725_0700_tech  export EDITION_BASENAME=20260725_0700_tech.mp3
  export EDITION_DATE_CN='2026年7月25日'   export EDITION_MONTH_EN=July
  export EDITION_SEARCH_AFTER=2026-07-23  export EDITION_MIN_SOURCE_DATE=2026-07-23
  export EDITION_DIR=<out-root>/20260725_0700_tech
  ```
- ⛔ **铁律：本次运行中出现的任何日期、年份、文件名，只能逐字复制上面的值。**
  - 禁止凭记忆或推理写年份；禁止自己做日期格式转换；禁止从 prompt 里的 `Current time:` 一行手工换算。
  - 需要口播日期用 `$EDITION_DATE_CN`，需要文件名用 `$EDITION_BASENAME`，需要搜索年份用 `$EDITION_YEAR`。
- 所有产物写进 `$EDITION_DIR`；文稿 `script.md`、来源 `sources.md`、成品 `$EDITION_BASENAME`。
- 日期规则：在档期前 30 分钟 ~ 后 3 小时内启动 → 归属该档（23:00 档在 00:20 运行仍归属前一天）；
  其余时间手动运行 → 一律归属**当天**（不会出现明天或昨天的日期）。
- **同时记下本次的 cron 任务 ID**：定时任务 prompt 第一行形如 `[cron:<任务ID> <任务名>] ...`，
  把 `<任务ID>` 原样记为 `$CRON_JOB_ID`，Step 6.5 要用。手动运行（非 cron）没有该行，留空即可。

### 1) Collect candidate news (20-30)
- Use 3-6 **English** queries to cover the topic.
- Call `web_search` **serially** with ≥2s delay.
- **每次调用必须带新鲜度与数量参数**（工具支持，但默认不启用 → 不带就会拿回大量陈旧条目）：
  - `count: 10`（覆盖 provider 默认的 5，扩大候选池）
  - `freshness: "day"`；若当档候选不足再放宽到 `date_after: $EDITION_SEARCH_AFTER`
- ⛔ **查询词里禁止硬编码年份/月份**（写死 `July 2025` 这类错误已发生过）。
  - 优先用相对表述：`this week` / `latest` / `today`。
  - 确需限定时间，只能用 `$EDITION_MONTH_EN $EDITION_YEAR`，不许手打。
- Keep for each item: title, snippet, source site, URL, **published date**。

### 2) Dedupe and rank → Top10
- Dedupe same event across sources; prefer official / Reuters / AP / gov / major outlets.
- **硬性新鲜度闸门（先筛后排）**：发布日期早于 `$EDITION_MIN_SOURCE_DATE` 的条目**直接丢弃**，不参与排名。新鲜度是准入条件，不是排序权重之一。
  - 若过滤后不足 10 条：先补搜（放宽到 `date_after: $EDITION_SEARCH_AFTER`），仍不足则按实际条数出刊并在 anomalies 说明，**不要用旧闻凑数**。
- 通过闸门后，再按 impact + credibility 排名。
- Internally store sources list (`$EDITION_DIR/sources.md`) for traceability，**每条必须写明发布日期**（`YYYY-MM-DD`，供 Step 6.5 校验）。

### 3) Write Chinese script（目标约 3分30秒）
- 写到 `$EDITION_DIR/script.md`。
- **开场白必须逐字使用 `$EDITION_DATE_CN`**，例如「今天是2026年7月25日」。这句会被 TTS 念出来，写错年份等于播错。
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

#### ⛔ 混音只能通过 `scripts/mix_bgm.sh`，禁止手写 ffmpeg
```bash
bash "$SKILL_DIR/scripts/mix_bgm.sh" <voice.mp3> <bgm.mp3> <out.mp3>
```
- **不得**为了"顺手"在 exec 里自己拼一条 ffmpeg 混音命令，**不得**在工作区另写 `manual_mix.sh` / `mix.sh` / `make_podcast.py` 之类的一次性脚本。
- 参数不合适就**改这个脚本**（它在 git 里，改动可追溯），不要绕过它。
- 原因（2026-07-28 真实事故）：手写命令 `-stream_loop -1 -i bgm.mp3 -t 190 -i voice_only.mp3`
  写出了一个 **59 GiB / 60 天时长**的 MP3，写满根分区，导致 Claude Code 无法写入凭据而登录失败。
  坑在于 **`-t` 写在 `-i` 前面是「输入选项」，绑定的是它后面那个输入**——所以 `-t 190` 截断的是人声，
  而无限循环的 BGM 完全没有上限；再加上 `amix duration=first` 的 first 恰好就是那条无限 BGM，
  输出端又没有 `-t`，ffmpeg 就一直写到磁盘满。
- `mix_bgm.sh` 现有三道互相独立的止损：`amix duration=shortest`（与输入顺序无关）、输出端 `-t`、输出端 `-fs` 字节上限，
  外加时长合理性预检、磁盘余量预检、以及产出后的时长比对（`-fs` 截断时 ffmpeg 仍返回 0，必须靠比对才能发现）。
- 另有系统级兜底：gateway/node 的 systemd `LimitFSIZE=2G`，任何 agent 写超 2 GB 的单文件都会被内核 SIGXFSZ 杀掉。

### 6.4) Resolve the model & write metadata（必须，在 6.5 之前）
- **`model_used` 不许由你填写，只能复制脚本输出。** 模型无法可靠说出自己的完整型号
  （以往只写出 `GPT-6`，或因"无法核实"留空），所以必须从 OpenClaw 的运行记录机械读取：
  ```
  python3 "$SKILL_DIR/scripts/resolve_model.py" --cron-job "$CRON_JOB_ID"    # 手动运行时省略 --cron-job
  ```
  输出 `MODEL_USED=openai/gpt-6.1-sol` 这类完整 `provider/model_id`；若中途发生 fallback，
  `MODEL_FALLBACK` 会列出 `主模型 -> 实际模型`，此时 `model_used` 写成 `$MODEL_USED（fallback，主模型 X 失败）`。
- 把完整 metadata 写入 `$EDITION_DIR/metadata.json`，把**将要发送的完整 caption** 写入 `$EDITION_DIR/caption.txt`
  （Step 7 发送时原样使用这份 caption），两处的 `model_used` 都逐字使用 `$MODEL_USED`。

### 6.5) Edition gate（必须，交付前的最后一道机械校验）
- 执行：
  ```
  python3 "$SKILL_DIR/scripts/verify_edition.py" --edition tech --dir "$EDITION_DIR" --cron-job "$CRON_JOB_ID"
  ```
  （politics 档用 `--edition politics`；Step 0 用了 `--date` 则这里也带同一个 `--date`；手动运行省略 `--cron-job`；
  产物不在 `$EDITION_DIR` 时用 `--audio/--script/--sources/--metadata/--caption` 显式指定。）
- 它会重新从系统时钟推导本期身份，并校验：文件名 == `$EDITION_BASENAME`、开场白含 `$EDITION_DATE_CN`、
  sources 里没有错年份的月份+年份组合、成品时长在 180–260 秒、
  metadata 与 caption 的 `model_used` == 运行记录里的实际模型（写 `GPT-6` 等家族名、漏标 fallback 都会 FAIL）。
- 来源新鲜度：**全部**来源早于 `$EDITION_MIN_SOURCE_DATE` → FAIL；**部分**过期 → WARN，
  此时必须回到 Step 2 把过期条目换掉（Step 2 的规则是直接丢弃），确实无法替换才在 anomalies 中说明。
- **exit 0 才允许交付。exit 1 = 禁止发送**，按报告里的 FAIL 项回到对应步骤修正后重跑本闸门；
  若无法修正，走 Step 9 失败上报。**绝不允许跳过本步或忽略其退出码。**
- 这一步不经过模型判断，是本流程唯一与模型能力无关的正确性保证。

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
  - `model_used` 必须等于 Step 6.4 `resolve_model.py` 输出的 `$MODEL_USED`（Step 6.5 已机械核对）。
  - 如果任一项不满足：不要发送"半成品"；应回到流程补齐（混 BGM / 补元数据），再发送
- caption **必须包含元数据**（metadata）与 **Top10 摘要列表**；不得只发 Top10 不发元数据
- **Filename must be exactly `$EDITION_BASENAME`**（Step 0 输出，已由 Step 6.5 校验）
- **必须使用绝对路径**（用 `$EDITION_DIR` / `$EDITION_AUDIO_FILE`），**禁止使用 `~`**（cron 环境不会展开）
  - **禁止使用**：`final.mp3`, `output.mp3`, `podcast.mp3` 等无意义名称
  - **禁止**手工拼写日期部分
- **默认不保存到 Dropbox**（除非用户显式要求）
- **禁止**把"Telegram 发送失败"作为触发条件去上传 Dropbox 兜底；失败就直接在群里报告失败原因与下一步
- **必须附带元数据**（同一条消息内，MP3文件作为附件，元数据放在caption中）：
  - 文稿模型：`model_used` = Step 6.4 的 `$MODEL_USED`，**逐字复制**完整 `provider/model_id`
    （如 `openai/gpt-6.1-sol`）。不得简写为 `GPT-6`、`GPT-6.1`、`GPT`、`Codex`，不得凭配置或记忆猜后缀（sol/terra/luna）。
    - 发生 fallback：写 `<实际模型>（fallback，主模型 <主模型> 失败）`，并在 anomalies 说明。
    - 仅当 `resolve_model.py` 退出码为 2 时才可写「未核实（无法取得当次实际模型 ID）」，并在 anomalies 说明原因。
    - caption、`metadata.json` 与 `podcast_xxx_last_ok.json` 的 `model_used` 必须一致。
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
- `scripts/edition_env.py`: **Step 0** — 从系统时钟推导本期唯一身份（日期/文件名/口播日期/新鲜度截止）。
- `scripts/resolve_model.py`: **Step 6.4** — 从 OpenClaw 运行记录（只读）取本次实际 `provider/model_id`，含 fallback 检测。
- `scripts/verify_edition.py`: **Step 6.5** — 交付前 fail-closed 校验（日期/文件名/时长/新鲜度/模型标注）；exit≠0 即禁止发送。
- `scripts/concat_opus.sh`: concat Opus + export voice-only MP3.
- `scripts/mix_bgm.sh`: produce final MP3 with BGM ducking.
- `/home/wzf/.openclaw/workspace/scripts/dropbox_download.py`: refresh token + download a Dropbox App Folder file.
  - ⚠️ **宿主相关**：此文件在本 skill 仓库之外，换机器需另行提供（仅影响 BGM 与可选的 Dropbox 投递，不影响 Step 0/6.5）。

## Portability
本 skill 力求自包含，可 clone 到其他机器 / 其他 coding agent 直接运行。
- **`scripts/` 下的脚本不含任何宿主绝对路径**：全部走参数，`--out-root` 默认取当前工作目录。
- **依赖**：`python3` ≥ 3.9（`zoneinfo`，纯标准库）、`ffmpeg`、`ffprobe`。与迁移前一致，未新增依赖。
  - 日期计算刻意用 python3 而非 `date -d`：后者是 GNU 扩展，在 macOS/BSD 上不可用。
- **时区**：默认 `Asia/Shanghai`，可用 `--tz` 覆盖，不依赖宿主 `TZ` 环境变量。
- **宿主相关部分**（换机器需适配，已在文中标注）：Telegram 投递（`message` / `sessions_send` 工具与 chatId）、
  Dropbox BGM 下载脚本、cron 调度配置。核心生成流程（Step 0–6.5）与宿主无关。
- `resolve_model.py` 读取 OpenClaw 的运行记录（`$OPENCLAW_STATE_DIR`，默认 `~/.openclaw`；不可用时退回 `openclaw sessions --json`）。
  非 OpenClaw 环境下它返回 exit 2（未核实），流程照常运行，Step 6.5 只给 WARN。

## Guardrails
- ⛔ **绝不凭记忆写日期/年份。** 唯一日期来源是 Step 0 的 `edition_env.py`；模型只允许复制它的输出。
  prompt 里的 `Current time:` 一行仅供参考，**不许据此手工换算**。
- ⛔ **绝不凭记忆或自我认知写 `model_used`。** 唯一来源是 Step 6.4 的 `resolve_model.py`。
- ⛔ **绝不跳过 Step 6.5，也不许忽略它的退出码。** 校验失败就是不发。
- Never auto-submit forms.
- Never paste secrets into chat.
- If Dropbox BGM path fails: treat as anomaly; attempt fallback only if user policy allows.
- **任何异常都必须发送 Telegram 反馈**（包括成功和失败），不允许静默。

## Scheduling contract (Important)
- **生成流程只记录在本 SKILL.md**；不要在定时任务里重复写流程细节。
- **定时任务只负责：时间 + 调用哪个 skill（podcast-pipeline）+ 必要的输入参数**（如 topic、日期/时间标签、chatId）。
- 若需要调整流程，**只改 SKILL.md**，不要改定时任务脚本/配置。
