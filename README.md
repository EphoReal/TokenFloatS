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
| **`THIS RUN`** | tokens consumed since TokenFloatS was opened. It is the difference between each tool's running total now and its total at launch, so it counts new usage only and starts at zero on every launch. |
| **`TOTAL`** | each tool's own lifetime figure, read straight from its data files. |

Only the tools you actually have installed get a row, so a user with two of them sees
two rows rather than four rows of red text. A tool that is installed but unreadable
(a changed schema, a corrupt database) is still reported, because that is a real
problem worth surfacing.

Cache reads typically account for around 80% of the grand total. Each row carries a
distribution bar breaking that tool's total into input, output, reasoning, cache read
and cache write.

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

---

## Supported tools

| Tool | Read from |
|---|---|
| Hermes | `%LOCALAPPDATA%\hermes\state.db`, table `session_model_usage` |
| opencode | `~/.local/share/opencode/opencode.db`, table `session_v2` |
| Cline | `~/.cline/data/sessions/*/*.json`, field `metadata.aggregateUsage` |
| DeepSeek Harness | `~/.dsh/storages/session_projcache`, field `rows.tokenUsage.val.totals` |
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
- **Compatibility**: the tray panel is Windows-only (uses `pystray` and `pywin32`).
  The collectors are plain stdlib and also run on macOS and Linux.


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

## License

MIT. See [LICENSE](LICENSE).

## Author

- Xiaohongshu / Rednote: @Epho
- GitHub: https://github.com/EphoReal