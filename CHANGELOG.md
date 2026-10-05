# Changelog

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