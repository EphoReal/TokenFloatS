[English](README.md) | [中文](README.zh-CN.md)

# TokenFloatS

One always-on-top panel showing live token usage for six AI coding tools at once:
**Hermes, opencode, Cline, DeepSeek Harness, Codex and Claude Code**.

Everything is read from each tool's local data files. **No network calls.** Watching your
usage spends no tokens and cannot slow down the tools it measures.

![Collapsed bar](docs/bar.png)

*The collapsed bar: the two figures worth having on screen at all times*

![Expanded panel](docs/panel.png)

*The expanded panel: one row per tool, each with its usage distribution*

![Run history](docs/runs.png)

*The run history: a bar per launch, and the recorded runs below it. Scroll the
chart sideways to reach older ones.*

---

## The two figures

| | Meaning |
|---|---|
| **`THIS RUN`** | tokens consumed since TokenFloatS was opened. It is the difference between each tool's running total now and its total at launch, so it counts new usage only and starts at zero on every launch. One exception is described under [Per-run accounting](#per-run-accounting). |
| **`TOTAL`** | each tool's own lifetime figure, read straight from its data files. |

Only the tools you actually have installed get a row, so a user with two of them sees
two rows rather than four rows of red text. A tool that is installed but unreadable
(a changed schema, a corrupt database) is still reported, because that is a real
problem worth surfacing.

Cache reads typically account for around 80% of the grand total. Each row carries a
distribution bar breaking that tool's total into input, output, reasoning, cache read
and cache write.

### What the buckets mean

| Bucket | Meaning |
|---|---|
| `input` | prompt tokens that were not served from cache |
| `output` | generated tokens |
| `reasoning` | thinking tokens, where the tool reports them separately |
| `cache_read` | prompt tokens replayed from the prompt cache (cheap) |
| `cache_write` | tokens written into the cache |

**fresh** = `input + output + reasoning` — the tokens that are not cache reads, and so
the ones that map to money. It is what `--once` reports alongside the total.

---

## Installation

Requires Python 3.10+.

```bash
git clone https://github.com/EphoReal/TokenFloatS
cd tokenfloats
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

## Running it

Double-click `TokenFloatS.cmd`. No console window appears.

The panel starts expanded. The collapse control in the upper right reduces it to the
bar shown above, and either state can be expanded again. Drag the window from
anywhere on its body; `Esc` collapses.

Tray icon menu: *Show panel* / *Refresh* / *Quit*. The close button hides the panel
without quitting.

Launching again while it is already running does not open a second copy; it brings the
existing panel back to the front.

It starts automatically with Windows if you put a shortcut to `TokenFloatS.cmd` in
`shell:startup`.

### Command line

```bash
python tokenfloats.py                 # start the tray app
python tokenfloats.py --once          # print one snapshot and exit
python tokenfloats.py --detect        # list which source paths were found
python tokenfloats.py --interval 3    # poll every 3 seconds for this run
```

---

## How often it reads

The sources are re-read every **10 seconds** by default, and the panel's footer shows
the time of the last poll.

A poll is cheap because the append-only logs are read incrementally (see
[Notes](#notes)): about **10 ms of CPU** once the caches are warm, roughly 0.1 % of one
core at the default interval and 0.4 % at the 2-second floor. Measured on a machine
with all six tools installed:

| Interval | CPU per poll | Share of one core |
|---|---|---|
| 2 s (the floor) | 8.6 ms | 0.42 % |
| 5 s | 11.7 ms | 0.23 % |
| 10 s (default) | 10.4 ms | 0.10 % |

Set it in `config.json`, or per run with `--interval SECONDS`, which wins over the file.

---

## Supported tools

| Tool | Read from |
|---|---|
| Hermes | `%LOCALAPPDATA%\hermes\state.db`, table `session_model_usage` |
| opencode | `~/.local/share/opencode/opencode.db`, table `session_v2` |
| Cline | `~/.cline/data/sessions/*/*.json`, field `metadata.aggregateUsage` |
| DeepSeek Harness | `~/.dsh/storages/session_projcache/sessions/*.json`, field `rows.tokenUsage.val.totals` |
| Codex | `~/.codex/sessions/**/*.jsonl`, field `info.total_token_usage` |
| Claude Code | `~/.claude/projects/**/*.jsonl`, field `message.usage` |

Only the six tools listed above are supported; TokenFloatS does not discover, infer or
auto-adapt to other harnesses.

A tool can legitimately live in more than one place, so each source has a short list
of candidate locations tried in order, with the first one that existing winning. An
explicit `config.json` override always takes precedence.

To see what was detected on a given machine:

```bash
venv\Scripts\python.exe tokenfloats.py --detect
```

The session count shown with `THIS RUN` is the number of sessions that recorded usage
— the same sessions the per-run figure is measured from, so the two always describe
the same set. A store holding 55 session files of which 12 never consumed anything
reads as 43.

DeepSeek Harness keeps an aggregate rollup at `~/.dsh/storages/session_projcache.json`,
one level *above* the directory. It is deliberately not read: it is a partial index —
on the machine this was written on it held 28 of the 55 sessions, and for 7 of those it
held a staler figure than the session file itself. The per-session files are a superset
by id and are rewritten every turn, so they alone are authoritative.

---

## Configuration

Optional. Put a `config.json` next to `tokenfloats.py` — `config.example.json` is a
starting point. A missing or malformed file just falls back to the defaults; nothing
raises.

```json
{
  "poll_seconds": 5,
  "paths": {
    "codex": "D:/somewhere/.codex/sessions",
    "DeepSeek Harness": "E:/dsh/storages/session_projcache"
  }
}
```

- `poll_seconds` — how often the sources are re-read. Default **10**, floor **2**.
  Below the floor the per-poll cost of stat-ing and globbing every store stops being
  worth the responsiveness. `--interval` overrides it.
- `paths` — overrides for any source. Keys may be either the short key (`hermes`,
  `opencode`, `cline`, `dsh`, `codex`, `claude_code`) or the displayed name (`Hermes`,
  `DeepSeek Harness`, `Claude Code`, …). `~`, `%VAR%` and `$VAR` are expanded.

`config.local.json` is ignored by git, for machine-specific overrides.

---

## Notes

- **Read-only**: every database is opened as
  `sqlite3.connect("file:...?mode=ro", uri=True)`, so TokenFloatS never creates a
  file and never blocks the tool that owns it.
- **Single instance**: guarded by an OS file lock, so a hard kill cannot leave a
  stale lock behind.
- **Reviewable**: each launch appends one dated record to `state/runs.json` with its
  start time, end time and per-tool figures, capped at the most recent 400 runs.
- **Diagnostics**: launches are windowless, so anything that goes wrong is appended
  to `tokenfloats.log` next to the script.
- **Incremental reads**: Codex and Claude Code keep one growing log per session, and a
  single Codex rollout log reaches tens of MiB. On the machine this was written on, 46
  rollout files totalled 135.6 MiB while only 3.6 MiB of their lines held token data,
  and re-reading them from the top cost ~230 ms of a ~500 ms poll — paid twice per
  poll, because the lifetime totals and the per-session snapshots each walked the same
  files. Each file's result is now cached against `(mtime, size)`, and a Codex log is
  read from where the last read stopped, taking the largest cumulative record seen. An
  unchanged file is not read at all, which took a warm poll from ~500 ms to ~48 ms.
- **Compatibility**: the tray panel is Windows-only (uses `pystray` and `pywin32`).
  The collectors are plain stdlib and also run on macOS and Linux.

---

## Run history

Every launch is recorded, whether it ends normally or not, so no session is ever
missing from the list. Each record keeps its start time, end time, what it spent
and which tools spent it, in `state/runs.json` next to the app, capped at the
most recent 400.

Open the run history at the bottom of the panel and the source rows step aside
for a bar chart and a table of the recorded runs. Each bar is one launch, as tall
as the tokens it spent, in the same blue that stands for input elsewhere in the
panel; a bar with a dashed edge is the run still in progress. The panel keeps its
size while the history is open, so nothing shifts on screen.

A record left open by a kill, a crash or a power cut is closed the next time the app
starts and marked `unclean`, so its end time is when the app was next opened rather
than a fabricated one.

### Per-run accounting

`THIS RUN` is measured by differencing per-session cumulative totals against a baseline
captured on the first poll, and it is held as a high-water mark so repeated polls
cannot double count.

A session absent from the launch baseline is counted **in full**, because a missing
baseline entry cannot be told apart from a brand-new session:

- a session created after launch starts at zero, so its total *is* its growth;
- a session **resumed from history** arrives already carrying its earlier totals, so
  that earlier part is reported as part of this run.

The second case is the deliberate trade. A source's session list is itself a partial
cache, so resuming a conversation the tool no longer lists is normal, and skipping such
a session silently lost its usage. The cost of the current rule is visible instead: the
first poll that sees a resumed session reports a one-off larger number.

---

## Tests

```bash
venv\Scripts\python.exe test_collectors.py       # no pytest needed
venv\Scripts\python.exe -m pytest test_collectors.py
```

They build their own fixtures in a temporary directory — never the real stores — and
redirect the run records with `TOKENFLOATS_STATE`, so running them cannot touch your
history.

---

## Troubleshooting

**Nothing appears.** Check `tokenfloats.log` next to the script: it is written on every
start, on every detected source, and on any uncaught exception, because a `pythonw`
launch has no stderr to print to. `startup-error.log` means the launch itself failed,
usually a missing dependency (run the install step above).

**A source shows nothing.** `python tokenfloats.py --detect` prints the path each source
resolved to. If a tool keeps its data somewhere unusual, override it in `config.json`.

**A figure looks stale.** The panel refreshes once per poll; the footer says when. Use
*Refresh* in the tray menu to force a poll.

## License

MIT. See [LICENSE](LICENSE).

## Author

- Xiaohongshu / Rednote: @Epho
- GitHub: https://github.com/EphoReal
