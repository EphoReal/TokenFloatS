# Changelog

All notable changes to TokenFloatS are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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