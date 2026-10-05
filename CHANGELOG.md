# Changelog

[English](#changelog) | [中文](#%E6%9B%B4%E6%96%B0%E6%97%A5%E5%BF%97%E4%B8%AD%E6%96%87)

All notable changes to TokenFloatS are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-10-05

### Fixed

- **Resuming an older conversation no longer loses its usage.** A session absent
  from the launch baseline contributed nothing, on the assumption that it was
  newly created. A resumed session arrives already carrying its earlier totals,
  so it was skipped entirely and its tokens never appeared in `THIS RUN`. Such a
  session is now counted in full the first time it is seen; the earlier part of
  its history is therefore reported as part of this run, which is the deliberate
  trade (see the README). Every source was affected, since a tool's session list
  is itself a partial cache.
- **Quitting no longer raises.** `finish_counter` was called on exit but never
  imported, so every quit from the tray menu raised `NameError` inside the
  `finally` block. The visible effect was that a clean exit never wrote its end
  time, and the next launch then had to mark the record `unclean`.
- **Missing sources are hidden again.** `Panel.refresh` called an undefined
  `_is_absent`. The distinction between "not installed" and "installed but
  unreadable" is now carried explicitly by `Usage.installed`, set by each
  collector, instead of being inferred from an error string.
- **A re-keyed or reset session is now counted in full, not by its first bucket
  alone.** When a counter went backwards the code concluded the session had been
  reset and added bucket 0's whole value, while still differencing buckets 1-4 —
  two readings of one session, which under-reported it. A reset from
  `[900, 800]` to `[10, 20]` is 30 tokens of new usage; it used to report 10. A
  baseline entry that is not a bucket list is also handled rather than raising.
- **Session counts now match the data behind them.** The figure beside
  `THIS RUN` disagreed with the per-session snapshots that produce it for three
  sources: Hermes counted rows in the `sessions` table rather than sessions with
  recorded usage (18 against 25), DeepSeek Harness counted sessions that never
  consumed anything (55 against 43), and Claude Code counted transcripts whose
  usage records were all zero (2 against 0). All six sources now count the
  sessions their snapshots hold, and the test suite asserts it.

### Changed

- **Polling is 10 times cheaper, and the interval defaults to 10 seconds**
  (was 60). Re-reading the append-only logs dominated every poll: 46 Codex
  rollout files totalling 135.6 MiB, of which 3.6 MiB of lines held token data,
  cost ~230 ms of a ~500 ms poll and were walked twice per poll. Each file's
  result is now cached against `(mtime, size)`, an unchanged file is not read at
  all, and a Codex log is read from where the last read stopped, keeping the
  largest cumulative record seen. A warm poll went from ~510 ms to ~48 ms,
  measured at ~10 ms of CPU — about 0.1 % of one core at the default interval.
- The poll interval is configurable via `config.json` (`poll_seconds`) or
  `--interval SECONDS`, with a floor of 2 seconds.
- The poll thread waits on a single `threading.Event`, so quitting no longer has
  to drain a sleep loop and now returns immediately.
- DeepSeek Harness: the aggregate rollup at
  `~/.dsh/storages/session_projcache.json` is explicitly not read. It is a
  partial index — 28 of 55 sessions, 7 of them staler than the session file — so
  the per-session files, which are a superset by id, are the sole source. The
  previous code looked for that file inside the directory, where it never
  existed, so this only makes the existing behaviour explicit.
- README: documents the bucket semantics, per-run accounting, configuration,
  the poll interval, the incremental reads, and troubleshooting. Added a test
  section and `config.example.json` now ships `poll_seconds: 10`.

### Removed

- Dead `_today()` helper, and a comment referring to `day_slice()`, a per-day
  figure removed before 1.0.0.

## [1.0.0] - 2026-10-03

First release.

### Added

- Run history. Every launch is recorded with its start time, end time, what it
  spent and which tools spent it, in `state/runs.json` (most recent 400). A run
  that was killed rather than quit is closed and marked when the app next opens.
- A bar chart of the recorded runs inside the run history: one bar per launch,
  one colour, scrolled horizontally through the whole record, with each bar's
  date and time on the axis.

- Always-on-top floating panel with a tray icon, showing live token usage for
  six AI coding tools: Hermes, opencode, Cline, DeepSeek Harness, Codex and
  Claude Code.
- Six collectors, each reading its tool's local data files read-only:
  - **Hermes**: `state.db`, table `session_model_usage`
  - **opencode**: `opencode.db`, table `session_v2`
  - **Cline**: `~/.cline/data/sessions/*/*.json`, field `metadata.aggregateUsage`, falling back to per-message `metrics`
  - **DeepSeek Harness**: `~/.dsh/storages/session_projcache`, field `rows.tokenUsage.val.totals`
  - **Codex**: `~/.codex/sessions/**/*.jsonl`, field `info.total_token_usage`
  - **Claude Code**: `~/.claude/projects/**/*.jsonl`, field `message.usage`
- Two headline figures side by side: `THIS RUN`, the tokens consumed since the
  app was opened, and `TOTAL`, each tool's own lifetime figure. A hover marker
  beside the first explains exactly how it is measured. A distribution bar breaks
  each total down into input, output, reasoning, cache read and cache write.
- Per-source usage distribution as a hairline stacked bar with an inline key,
  plus a grand total across all sources.
- **Collapsed and expanded states.** Starts expanded with the full per-tool
  breakdown; the collapse control reduces it to a slim bar carrying the two
  headline figures, and either state can be dragged.
- **Absent sources are hidden.** Only the tools you actually have installed get
  a row. Absent and broken are distinguished, so a tool that is installed but
  unreadable is still reported inline. Absent sources remain visible in
  `--detect` and in `tokenfloats.log`.
- Rounded corners and the Windows 11 system backdrop (acrylic, falling back to
  mica) from the DWM. `wineffects.py` isolates the Win32 calls, each degrades
  silently, and `capabilities()` reports what the host supports.
- Panel height auto-fits its content, so a source is never clipped.
- One dated record per launch in `state/runs.json`, carrying the start time, end
  time and per-tool figures, capped at the most recent 400 runs.
- The close button hides the panel without quitting, and the tray menu offers
  show / refresh / quit. Refreshes every 60 seconds.
- Single-instance guard: relaunching surfaces the running panel instead of
  creating a duplicate tray icon and a second Tk root. Backed by an OS file lock
  rather than a PID file, so a hard kill cannot leave a stale lock behind.
- `tokenfloats.log` next to the script, recording starts, lock events and any
  uncaught exception from the main thread or a worker thread, since a
  windowless `pythonw` launch has no stderr to show them.
- `config.json` path overrides with `~` and `%VAR%` expansion, and
  `config.local.json` which is git-ignored for machine-specific paths.
- Per-source candidate path lists, first existing candidate wins, so a tool
  installed outside its default location is still found. `tokenfloats.py
  --detect` prints what was found and what was expected.
- `--once` for a GUI-free snapshot; `collectors.py` runs standalone with a full
  text report and distribution bars.

### Notes

- No network calls anywhere: polling spends no tokens and cannot slow down the
  tools it measures.
- Every database is opened with `sqlite3.connect("file:...?mode=ro", uri=True)`,
  so TokenFloatS never creates a file, never blocks the writer and never takes a
  lock that could stall the tool being measured.
- Only the six listed tools are supported. TokenFloatS does not discover, infer or
  auto-adapt to other harnesses.

---

# 更新日志（中文）

TokenFloatS 的所有重要变更都记录在此。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

[中文](#%E6%9B%B4%E6%96%B0%E6%97%A5%E5%BF%97%E4%B8%AD%E6%96%87) | [English](#changelog)

本文件的中文版本为英文版本的翻译，内容与上方英文部分对应。

## [1.1.0] - 2026-10-05

### 修复

- **接续旧对话不再丢失用量。** 原先，若某个会话不在启动基线中，就被视为新建会话、
  因此不计入任何用量。但接续的历史会话出现时就已经带着此前的累计值，于是它被整体
  跳过，那部分 token 永远不会出现在 `THIS RUN` 里。现在这类会话在第一次被看到时
  按当前总量整体计入；也就是说它启动前的那段历史会被算进本次运行——这是刻意的
  取舍（详见 README）。六个来源都受此问题影响，因为工具自身的会话列表本身就是
  一份部分缓存。
- **退出不再抛异常。** `finish_counter` 在退出时被调用，却从未被导入，因此每次从
  托盘菜单退出都会在 `finally` 块中抛出 `NameError`。可见后果是：正常退出从不写入
  结束时间，下一次启动只能把这条记录标成 `unclean`。
- **未安装的来源重新被隐藏。** `Panel.refresh` 调用了一个未定义的 `_is_absent`。
  现在「未安装」与「已安装但读取失败」的区别由 `Usage.installed` 显式承载（各采集器
  负责设置），不再从错误字符串里反推。
- **被重新建键或重置的会话按全部桶计入，而不是只算第一个桶。** 原先发现某个计数器
  回退时，代码判定该会话已被重置并加上桶 0 的整值，却仍对桶 1–4 做差值——同一个
  会话被用了两套读法，导致少计。一次从 `[900, 800]` 到 `[10, 20]` 的重置是 30 个
  新增 token，旧逻辑只报 10。此外，基线中不是桶列表的条目现在会被正常处理，而不是
  抛异常。
- **会话数与它背后的数据对齐。** `THIS RUN` 旁的会话数与产出该数字的逐会话快照在
  三个来源上不一致：Hermes 数的是 `sessions` 表的行数而非有记录的会话（18 对 25），
  DeepSeek Harness 把从未产生消耗的会话也算了进去（55 对 43），Claude Code 把用量
  记录全为 0 的记录文件也当成会话（2 对 0）。现在六个来源都按快照实际持有的会话
  计数，并有测试断言这一点。

### 变更

- **轮询成本降低约十倍，默认间隔改为 10 秒**（原为 60 秒）。此前每轮轮询的开销主要
  来自重读追加型日志：46 个 Codex rollout 文件共 135.6 MiB，其中只有 3.6 MiB 的行
  带有 token 数据，却要花掉约 500 ms 轮询中的 230 ms，而且每轮要走两遍。现在每个
  文件的结果按 `(mtime, size)` 缓存，未变化的文件完全不读，Codex 日志从上次读完的
  位置继续读，并保留见过的最大累计记录。热轮询从约 510 ms 降到约 48 ms，实测约
  10 ms CPU——默认间隔下约占单核 0.1%。
- 轮询间隔可通过 `config.json`（`poll_seconds`）或 `--interval 秒数` 配置，下限为
  2 秒。
- 轮询线程改为等待单个 `threading.Event`，因此退出不再需要耗完一轮 sleep，可以立即
  返回。
- DeepSeek Harness：`~/.dsh/storages/session_projcache.json` 这份聚合汇总被明确
  设为不读取。它只是部分索引——55 个会话中只有 28 个，其中 7 个的数值还比逐会话
  文件更旧——因此按 ID 属于超集的逐会话文件是唯一来源。旧代码是在该目录内部寻找
  这个文件（那里从来不存在），所以这一改动只是把既有行为写明。
- README：补充了五个桶的语义、本次运行的统计口径、配置说明、读取频率、增量读取与
  排障；新增测试章节，且 `config.example.json` 现在给出 `poll_seconds: 10`。

### 移除

- 已无引用的 `_today()` 辅助函数，以及一处指向 `day_slice()` 的注释——那是 1.0.0
  之前就移除的按天统计功能留下的。

## [1.0.0] - 2026-10-03

首个版本。

### 新增

- 运行历史。每次启动都会记录开始时间、结束时间、本次消耗以及消耗来自哪些工具，存放
  在 `state/runs.json`（最多保留最近 400 条）。被强杀而非正常退出的运行，会在下次
  启动时被补上结束时间并标记。
- 运行历史内的柱状图：每次启动一根柱子、单一配色，可横向滚动查看全部记录，横轴标注
  每根柱子的日期和时间。

- 置顶悬浮面板与托盘图标，实时显示六个 AI 编程工具的 token 用量：Hermes、opencode、
  Cline、DeepSeek Harness、Codex、Claude Code。
- 六个采集器，各自只读地解析对应工具的本地数据文件：
  - **Hermes**：`state.db`，表 `session_model_usage`
  - **opencode**：`opencode.db`，表 `session_v2`
  - **Cline**：`~/.cline/data/sessions/*/*.json`，字段 `metadata.aggregateUsage`，缺失时回退到逐条消息的 `metrics`
  - **DeepSeek Harness**：`~/.dsh/storages/session_projcache`，字段 `rows.tokenUsage.val.totals`
  - **Codex**：`~/.codex/sessions/**/*.jsonl`，字段 `info.total_token_usage`
  - **Claude Code**：`~/.claude/projects/**/*.jsonl`，字段 `message.usage`
- 并排显示的两个核心数字：`THIS RUN`（本次打开以来消耗的 token）与 `TOTAL`（各工具
  自身的历史总量）。前者旁的悬停标记会说明它的确切算法。分布条把每个总量拆成
  input、output、reasoning、cache read、cache write。
- 每个来源的用量分布以发丝级堆叠条展示，内置图例，并给出跨来源的合计。
- **折叠与展开两种状态。** 默认展开显示逐工具明细；收缩控件把它变成只带两个核心数字
  的窄条，两种状态都可以拖动。
- **未安装的来源会被隐藏。** 只有你确实装了的工具才有行。「未安装」与「损坏」被区分
  开来，已安装但读取失败的工具仍会在面板内行内报告。未安装的来源仍可在 `--detect`
  与 `tokenfloats.log` 中看到。
- 来自 DWM 的圆角与 Windows 11 系统背景（亚克力，回退到云母）。`wineffects.py`
  隔离了 Win32 调用，每一步失败都静默降级，`capabilities()` 报告宿主支持情况。
- 面板高度自适应内容，任何来源都不会被裁掉。
- 每次启动在 `state/runs.json` 中留下一条带日期的记录，含开始时间、结束时间与各工具
  数字，最多保留最近 400 次。
- 关闭按钮只隐藏面板而不退出，托盘菜单提供显示 / 刷新 / 退出。每 60 秒刷新一次。
- 单实例保护：重复启动会把已有面板唤到前台，而不是产生重复的托盘图标和第二个 Tk
  根窗口。依托操作系统文件锁而非 PID 文件，因此强杀进程不会留下失效的锁。
- 脚本同目录的 `tokenfloats.log`，记录启动、锁事件以及主线程或工作线程的未捕获异常，
  因为无窗口的 `pythonw` 启动没有 stderr 可以显示它们。
- `config.json` 路径覆盖，支持 `~` 与 `%VAR%` 展开；`config.local.json` 被 git 忽略，
  用于存放机器相关的路径。
- 每个来源的候选路径列表，取第一个存在的，因此装在非默认位置的工具也能被发现。
  `tokenfloats.py --detect` 会打印探测到了什么、以及期望在哪里。
- `--once` 用于无界面快照；`collectors.py` 也可独立运行，输出完整文本报告与分布条。

### 说明

- 全程不发起任何网络请求：轮询不消耗 token，也不会拖慢被监测的工具。
- 所有数据库都以 `sqlite3.connect("file:...?mode=ro", uri=True)` 打开，因此
  TokenFloatS 绝不创建文件、不阻塞写入方，也不会持有可能卡住被监测工具的锁。
- 仅支持上表列出的六个工具，不会自动探测、推断或适配其他 harness。
