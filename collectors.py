"""Token usage collectors for Hermes, opencode, Cline, DeepSeek Harness and Codex.

Every source is a local SQLite/JSON file, opened READ-ONLY. Nothing here calls a
network API, so polling costs no tokens and the tool cannot slow down the tools
it measures.

Paths default to each tool's standard location and can be overridden per source
in config.json (see PATHS below), so the collector also works when a tool keeps
its data somewhere non-standard.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
APPDATA = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
XDG_DATA = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")

# Default locations. Each entry may be overridden by config.json:
#   {"paths": {"codex": "D:/somewhere/.codex/sessions", ...}}
# Candidate locations per source, highest priority first. A tool can legitimately
# live in more than one place: the WorkBuddy case on this machine has two
# independent profile directories, and Codex ships both a Store app and a CLI.
# "First existing candidate wins" keeps that explicit instead of guessing.
CANDIDATES: dict[str, tuple[str, ...]] = {
    "hermes": (
        os.path.join(LOCALAPPDATA, "hermes", "state.db"),
    ),
    "opencode": (
        os.path.join(XDG_DATA, "opencode", "opencode.db"),
        os.path.join(APPDATA, "opencode", "opencode.db"),
        os.path.join(HOME, ".opencode", "opencode.db"),
    ),
    "cline": (
        os.path.join(HOME, ".cline", "data", "sessions"),
    ),
    "dsh": (
        os.path.join(HOME, ".dsh", "storages", "session_projcache"),
    ),
    "codex": (
        os.path.join(HOME, ".codex", "sessions"),
    ),
    "claude_code": (
        os.path.join(HOME, ".claude", "projects"),
    ),
}

DEFAULTS: dict[str, str] = {k: v[0] for k, v in CANDIDATES.items()}


def load_config(path: str | None = None) -> dict:
    """Read config.json if present. Never raises - a broken file just falls back."""
    for candidate in (path, os.path.join(APP_DIR, "config.json"),
                      os.path.join(APP_DIR, "config.local.json")):
        if candidate and os.path.exists(candidate):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def _expand(path: str) -> str:
    """Expand ~ and %VAR% / $VAR so config.json can stay copy-pasteable."""
    text = os.path.expandvars(path)
    if os.altsep:                       # on Windows os.sep is '\\', altsep '/'
        text = text.replace(os.altsep, os.sep)
    return os.path.expanduser(text)


# The panel labels each source for the user ("DeepSeek Harness"), while the
# paths are filed under a short key ("dsh"). Both spellings have to resolve, or a
# --detect run or a config override silently finds nothing.
SOURCE_ALIASES = {
    "hermes": "hermes",
    "opencode": "opencode",
    "cline": "cline",
    "dsh": "dsh",
    "deepseek": "dsh",
    "deepseek harness": "dsh",
    "codex": "codex",
    "claude_code": "claude_code",
    "claude": "claude_code",
    "claudecode": "claude_code",
    "claude code": "claude_code",
}

# The panel's own spelling, which differs from the key in two places.
DISPLAY_TO_KEY = {
    "Hermes": "hermes",
    "opencode": "opencode",
    "Cline": "cline",
    "DeepSeek Harness": "dsh",
    "Codex": "codex",
    "Claude Code": "claude_code",
}


def source_key(source: str) -> str:
    """Accept the displayed name, the short key, or a lower-cased version."""
    return (DISPLAY_TO_KEY.get(source)
            or SOURCE_ALIASES.get(source.lower(), source))


def path_for(source: str, config: dict | None = None) -> str:
    """Resolve a source to a concrete path.

    An explicit config override always wins. Otherwise the first existing
    candidate is used, falling back to the highest-priority one so the error
    message names a real expected location rather than an empty string.
    """
    key = source_key(source)
    cfg = config if config is not None else load_config()
    override = (cfg.get("paths") or {}).get(source) or \
        (cfg.get("paths") or {}).get(key)
    if override:
        return _expand(str(override))
    candidates = CANDIDATES.get(key) or ()
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0] if candidates else DEFAULTS.get(key, "")


# The reverse of SOURCE_ALIASES: the name the panel shows for each short key.
KEY_TO_DISPLAY = {
    "hermes": "Hermes",
    "opencode": "opencode",
    "cline": "Cline",
    "dsh": "DeepSeek Harness",
    "codex": "Codex",
    "claude_code": "Claude Code",
}


def display_name(key: str) -> str:
    return KEY_TO_DISPLAY.get(key, key)


def detect_all(config: dict | None = None) -> dict:
    """Report which candidate paths actually exist: ({found}, {missing}).

    Keys are the displayed names, so --detect and the panel agree on what each
    tool is called.
    """
    cfg = config if config is not None else load_config()
    found: dict[str, str] = {}
    missing: dict[str, tuple[str, ...]] = {}
    for source, candidates in CANDIDATES.items():
        override = (cfg.get("paths") or {}).get(source)
        name = display_name(source)
        if override:
            path = _expand(str(override))
            if os.path.exists(path):
                found[name] = path
            else:
                missing[name] = (path,)
            continue
        hits = [c for c in candidates if os.path.exists(c)]
        if hits:
            found[name] = hits[0]
            if len(hits) > 1:
                missing["+also " + name] = tuple(hits[1:])
        else:
            missing[name] = tuple(candidates)
    return found, missing


# ====================================================== per-run records
#
# The headline figure answers one question: how much has been spent since THIS
# process started. It is measured by differencing each source's per-session
# cumulative totals against the values observed at launch, so it counts growth
# only and can never re-count history.
#
# Every run appends one record - the launch date and time, and what the run went
# on to spend. That is honest in a way a per-day figure was not: it never has to
# invent a midnight boundary or pretend to know what happened while the app was
# shut. Summing the runs of one calendar day gives that day's true figure for
# every period the app was actually open.
#
# Records are kept in a single small JSON file, rewritten on every poll so a
# crash still leaves the run that crashed, and pruned to the most recent entries.

BASELINE: dict | None = None       # snapshots taken at launch
BANKED: dict[str, int] = {}        # per-source growth accumulated this run
RUN_START: str = ""                # "YYYY-MM-DD HH:MM"
MAX_RUNS = 400                     # ~400 runs; a few tens of KB
# Ceiling on tracked sessions per source. Without it the launch snapshot grows
# with every session a tool has ever had.
MAX_SESSIONS_PER_SOURCE = 400


# ============================================ append-only log file caching
#
# Codex and Claude Code keep one growing log file per session, and a single
# Codex rollout log reaches tens of MiB. Reading them from the top on every poll
# means reading hundreds of MiB to extract a few KiB of usage: measured on this
# machine, 46 rollout files of 135.6 MiB hold only 3.6 MiB of token lines, and
# that full scan was ~230 ms of a ~500 ms poll, paid twice per poll because the
# lifetime totals and the per-session snapshots each walk the same files.
#
# So each file's result is cached against (mtime, size). An untouched file is
# never read again; a file that grew is read once instead of twice; and a Codex
# log (whose usage records are cumulative) is read from where the last read
# stopped rather than from the top.
#
# The cache is keyed by absolute path, so two configs - or a test's temp
# fixtures - can never collide. It holds small dicts only; nothing retains file
# contents.
_FILE_RESULTS: dict[tuple, dict] = {}


def _file_stamp(path: str) -> tuple[int, int] | None:
    """(mtime_ns, size) for a file, or None when it cannot be stat'ed."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _cached_file_scan(path: str, read_fn, stamp_extra=None) -> dict:
    """`read_fn(open_file) -> dict` cached until the file's stamp changes.

    read_fn is handed the open file and returns the buckets it found; it keeps
    whatever offset state it needs in the module-level dicts below.
    stamp_extra is folded into the cache key by callers whose read is
    incremental, so a stale entry can never be reached for a different reading
    position even when the filesystem's mtime resolution is coarse.
    """
    stamp = _file_stamp(path)
    if stamp is None:
        return {}
    key = (os.path.abspath(path), stamp[0], stamp[1], stamp_extra)
    hit = _FILE_RESULTS.get(key)
    if hit is not None:
        return hit
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            result = read_fn(fh)
    except OSError:
        return {}
    result = result or {}
    if len(_FILE_RESULTS) > 2048:              # bounded: drop the oldest half
        for old in list(_FILE_RESULTS)[:1024]:
            _FILE_RESULTS.pop(old, None)
    _FILE_RESULTS[key] = result
    return result


# Offset state for the incremental Codex scan, keyed by absolute path.
_CODEX_OFFSET: dict[str, int] = {}
# The winning record so far, held as (total_tokens, buckets). The comparison key
# has to be kept alongside the buckets: comparing a new record against
# `latest["total_tokens"]` would fail, because total_tokens is deliberately not
# one of the bucket names carried forward.
_CODEX_TOTAL: dict[str, tuple[int, dict]] = {}

# Bucket names under which Codex reports usage in a token_count record.
CODEX_BUCKETS = ("input_tokens", "output_tokens", "cached_input_tokens",
                 "cache_write_input_tokens", "reasoning_output_tokens")


def _codex_read_from(fh, start: int) -> dict:
    """Scan from byte offset `start` and fold the result into the running max.

    Codex writes total_token_usage as a CUMULATIVE per-session figure, so across
    polls the largest record wins; earlier bytes therefore never need revisiting.
    A later but smaller record must not lower the figure, so the record already
    held is compared against the new ones by its own total_tokens.
    """
    path = getattr(fh, "name", "")
    key = os.path.abspath(path)
    prior = _CODEX_TOTAL.get(key)
    if prior:
        best_total, latest = prior[0], dict(prior[1])
    else:
        best_total, latest = -1, None
    fh.seek(start)
    for line in fh:
        if '"token_count"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        pl = d.get("payload") or {}
        if pl.get("type") != "token_count":
            continue
        tcu = (pl.get("info") or {}).get("total_token_usage")
        if not isinstance(tcu, dict) or "total_tokens" not in tcu:
            continue
        if tcu["total_tokens"] >= best_total:
            best_total = tcu["total_tokens"]
            latest = {k: tcu.get(k) or 0 for k in CODEX_BUCKETS}
    try:
        _CODEX_OFFSET[key] = fh.tell()
    except (OSError, ValueError):
        _CODEX_OFFSET[key] = 0
    if latest:
        _CODEX_TOTAL[key] = (best_total, dict(latest))
    return latest or {}


def _codex_file_buckets(path: str) -> dict:
    """Latest cumulative usage in one rollout log, resuming from the last read.

    Skips the file entirely when it has not grown since the last read - that is
    the common case for the tens of rollouts that are no longer active - and
    otherwise reads only the bytes appended since. Reading a tail is cheap; the
    JSON decoding is what costs, and that now happens once per new record rather
    than once per poll per file.
    """
    key = os.path.abspath(path)
    start = _CODEX_OFFSET.get(key, 0)
    try:
        size = os.path.getsize(path)
    except OSError:
        return {}
    if start > size:                      # truncated or replaced: start over
        start = 0
        _CODEX_TOTAL.pop(key, None)
    # An unchanged file resolves to the same cache key and is not read again.
    # The stamp includes `start` so a stale entry cannot be reached from a
    # different reading position, even if mtime resolution is coarse.
    return _cached_file_scan(path, lambda fh: _codex_read_from(fh, start),
                             stamp_extra=start)



def _state_dir() -> str:
    """Where the run record lives.

    TOKENFLOATS_STATE redirects it, so anything that imports this module (a test,
    a one-off script) keeps its records elsewhere. Without the override every such
    program wrote into the user's real history, and a test that tidied up after
    itself deleted it.
    """
    override = os.environ.get("TOKENFLOATS_STATE")
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")


def _atomic_write(path: str, payload) -> None:
    """Replace a file in one step, so a crash cannot truncate it."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def arm_counter(snapshots: dict) -> None:
    """Take the launch baseline and open a run record. Until this is called,
    growth is not counted."""
    global BASELINE, BANKED, RUN_START
    BASELINE = snapshots or {}
    BANKED = {}
    RUN_START = _now_stamp()
    runs = _load_runs()

    # A previous launch killed rather than closed leaves its record open, and the
    # panel would show it as still running for ever. Close those before opening
    # this one, so only the current run reads as live.
    stamp = _now_stamp()
    for r in runs:
        if isinstance(r, dict) and not r.get("end"):
            r["end"] = stamp
            r["unclean"] = True

    runs.append({"start": RUN_START, "end": "", "by_source": {}, "total": 0})
    _save_runs(runs)


def counter_active() -> bool:
    return BASELINE is not None


def _runs_path() -> str:
    return os.path.join(_state_dir(), "runs.json")


def _load_runs() -> list:
    try:
        with open(_runs_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        runs = data.get("runs") if isinstance(data, dict) else data
        return runs if isinstance(runs, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_runs(runs: list) -> None:
    runs = runs[-MAX_RUNS:]
    try:
        _atomic_write(_runs_path(), {"runs": runs})
    except OSError:
        pass


def recent_runs(limit: int = 30) -> list:
    """Recorded runs, newest first."""
    return list(reversed(_load_runs()[-limit:]))


def finish_counter() -> None:
    """Close the open run record so its end time is on disk."""
    if not RUN_START:
        return
    runs = _load_runs()
    if runs and runs[-1].get("end") == "":
        runs[-1]["end"] = _now_stamp()
        _save_runs(runs)


def close_stale_runs() -> int:
    """Give every record still open an end time, and say how many were closed.

    A record is closed on a clean exit, but a kill, a crash or a power cut leaves
    it open, and then the panel shows it as still running for ever. Nothing in
    the record says when it was last touched, so the best honest answer is this
    moment: the run was going until the app was next opened.
    """
    stamp = _now_stamp()
    runs = _load_runs()
    closed = 0
    for r in runs:
        if isinstance(r, dict) and not r.get("end"):
            r["end"] = stamp
            r["unclean"] = True      # says why, rather than pretending
            closed += 1
    if closed:
        _save_runs(runs)
    return closed


def session_growth(current: dict, previous: dict) -> dict:
    """Per-source growth between two sets of per-session cumulative snapshots.

    When a session is absent from `previous`, its whole current value is counted.
    A missing baseline entry cannot be told apart from a brand-new session, and
    treating it as new is the only reading that does not silently lose usage:

      * a session created after launch starts at zero, so counting its total IS
        its growth - there is nothing earlier to double count;
      * a session resumed from history arrives carrying its earlier totals. The
        part spent before this run gets reported as part of this run, which
        overstates one figure. That is the deliberate trade: under-reporting
        (skipping the session outright) was invisible and wrong, while this
        shows up as a one-off larger number the user can see and judge.

    This matters in practice because a source's session list is itself a
    partial cache: resuming a conversation it no longer holds produces exactly
    this shape - a session that is already large the first time it is seen.
    The caller's high-water mark keeps that one-off figure from ever decreasing,
    so the overstatement persists for the life of the run rather than decaying.

    A whole value is also what a re-keyed or reset session gets. Reading "the
    session was reset" from one counter and then accounting the others as plain
    differences mixes two readings of the same session and under-reports it: a
    reset from [900, 800] to [10, 20] is 30 tokens of new usage, not the 10 that
    differencing bucket 0 alone would give.
    """
    out: dict[str, int] = {}
    for source, sessions in (current or {}).items():
        before = (previous or {}).get(source) or {}
        n = 0
        for key, vals in sessions.items():
            total = sum(vals)
            old = before.get(key)
            if not isinstance(old, list) or not old:
                # Not seen at launch, or nothing usable recorded for it: what it
                # holds now is what it has consumed since.
                n += total
                continue
            if vals and vals[0] > 0 and vals[0] < (old[0] if old else 0):
                # The session's own counter went backwards, so its key was
                # re-used or the session was reset. The whole session is new
                # usage now, exactly as in the not-seen-at-launch case above.
                n += total
                continue
            for idx in range(5):
                cur = vals[idx] if idx < len(vals) else 0
                prior = old[idx] if idx < len(old) else 0
                if cur > prior:
                    n += cur - prior
        out[source] = n
    return out


def since_launch(current: dict) -> dict:
    """Growth since launch. Returns {source: tokens}.

    Every poll measures from the SAME launch baseline, so each result is already
    the cumulative figure for this run. Adding them together would count the
    first poll's growth again on every later poll, so the high-water mark is kept
    instead: it is monotone by construction and cannot drift.

    The open run record is updated in the same pass, so the log on disk always
    shows this run's current figure rather than only what it had at exit.
    """
    if BASELINE is None:
        return {}
    cumulative = session_growth(current, BASELINE)
    for source, n in cumulative.items():
        if n > BANKED.get(source, 0):
            BANKED[source] = n

    runs = _load_runs()
    if runs:
        runs[-1]["by_source"] = dict(BANKED)
        runs[-1]["total"] = sum(BANKED.values())
        _save_runs(runs)
    return dict(BANKED)


# ------------------------------------------------- per-session snapshots
#
# Per-run accounting needs, for each source, the cumulative token counts of
# every session, keyed by something stable across polls. The lifetime totals are
# computed from exactly the same numbers - each source's collector and its
# snapshot read one shared function - so the two figures the panel shows can
# never disagree.

def _snapshot_hermes(config: dict | None = None) -> dict:
    db = path_for("hermes", config)
    if not os.path.exists(db):
        return {}
    out: dict[str, list[int]] = {}
    try:
        con = _ro(db)
        for r in con.execute("""
                SELECT session_id, model, input_tokens, output_tokens,
                       cache_read_tokens, cache_write_tokens, reasoning_tokens
                FROM session_model_usage"""):
            # task and billing columns would fragment the key; session+model is
            # the axis usage is actually tracked on
            key = f"{r[0]}|{r[1]}"
            i, o, cr, cw, rs = (r[2] or 0, r[3] or 0, r[4] or 0, r[5] or 0, r[6] or 0)
            prev = out.get(key)
            out[key] = [i, o, cr, cw, rs] if not prev else [
                prev[j] + v for j, v in enumerate((i, o, cr, cw, rs))]
        con.close()
    except Exception:                                  # noqa: BLE001
        return {}
    return out


def _snapshot_opencode(config: dict | None = None) -> dict:
    db = path_for("opencode", config)
    if not os.path.exists(db):
        return {}
    out: dict[str, list[int]] = {}
    try:
        con = _ro(db)
        for r in con.execute("""
                SELECT id, tokens_input, tokens_output, tokens_cache_read,
                       tokens_cache_write, tokens_reasoning FROM session_v2"""):
            out[r[0]] = [r[1] or 0, r[2] or 0, r[3] or 0, r[4] or 0, r[5] or 0]
        con.close()
    except Exception:                                  # noqa: BLE001
        return {}
    return out


def _cline_metrics(node: dict) -> list[int]:
    """Token counts from one usage node, in either spelling.

    Cline renamed the fields and moved the totals: per-message ``metrics``
    became a session-level ``aggregateUsage``. Both spellings are still in the
    wild across versions, so accept either rather than reporting zero.
    """
    if not isinstance(node, dict):
        return [0, 0, 0, 0, 0]
    pick = lambda *names: next(                # noqa: E731
        (node[n] for n in names if isinstance(node.get(n), int)), 0)
    return [
        pick("inputTokens", "input_tokens", "uncachedInputTokens"),
        pick("outputTokens", "output_tokens"),
        pick("cacheReadTokens", "cache_read_input_tokens", "cacheRead"),
        pick("cacheWriteTokens", "cache_creation_input_tokens", "cacheWrite"),
        pick("reasoningTokens", "reasoning_tokens"),
    ]


def _snapshot_cline(config: dict | None = None) -> dict:
    root = path_for("cline", config)
    if not os.path.isdir(root):
        return {}
    out: dict[str, list[int]] = {}
    for d in glob.glob(os.path.join(root, "*")):
        if not os.path.isdir(d):
            continue
        sid = os.path.basename(d)

        # Preferred source: the session file's running total. Cline keeps the
        # authoritative aggregate there, so no summing and no double counting.
        acc = [0, 0, 0, 0, 0]
        for f in glob.glob(os.path.join(d, "*.json")):
            if f.endswith(".messages.json"):
                continue
            try:
                with open(f, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            agg = (meta.get("metadata") or {}).get("aggregateUsage")
            if isinstance(agg, dict):
                acc = _cline_metrics(agg)
                break
        else:
            # Older layout: the messages file is a list, and every message
            # carries its own metrics. Sum them.
            for f in glob.glob(os.path.join(d, "*.messages.json")):
                try:
                    with open(f, encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                items = data if isinstance(data, list) else \
                    (data.get("messages") or [])
                for m in items:
                    if not isinstance(m, dict):
                        continue
                    mt = m.get("metrics")
                    if not isinstance(mt, dict):
                        continue
                    part = _cline_metrics(mt)
                    acc = [a + b for a, b in zip(acc, part)]

        if any(acc):
            out[sid] = acc
    return out


def _dsh_sessions(config: dict | None = None) -> dict[str, list[int]]:
    """Per-session DSH usage: {session_id: [in, out, cache_read, cache_write, 0]}.

    Only sessions that actually consumed something are returned. DSH creates a
    session file as soon as a conversation exists, so a store holds many
    zero-token entries; counting those as "sessions" would overstate what the
    panel says it is reading.

    One basis for both the lifetime totals and the per-session snapshots, so the
    two figures the panel shows can never disagree.

    session_projcache.json - the rollup one level up, at ~/.dsh/storages/ - is
    deliberately NOT read. It is a partial index: measured here it held 28 of the
    55 sessions, and for 7 sessions it held a staler figure than the session file
    (e.g. 2,929,277 against 5,404,838). The per-session files are a superset by
    id and are rewritten every turn, so they alone are authoritative.
    """
    per_dir = os.path.join(path_for("dsh", config), "sessions")
    if not os.path.isdir(per_dir):
        return {}

    out: dict[str, list[int]] = {}
    for f in glob.glob(os.path.join(per_dir, "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        rows = ((doc.get("record") or {}).get("rows") or {})
        # tokenUsage is a SIBLING of sessionStats under rows, wrapped in
        # ver/seq/val - it is NOT nested inside sessionStats, and the input field
        # is uncachedInputTokens, not inputTokens.
        tu = rows.get("tokenUsage") or {}
        val = tu.get("val") if isinstance(tu.get("val"), dict) else tu
        totals = val.get("totals") if isinstance(val, dict) else None
        if not isinstance(totals, dict):
            continue
        acc = [totals.get("uncachedInputTokens") or 0,
               totals.get("outputTokens") or 0,
               totals.get("cacheReadTokens") or 0,
               totals.get("cacheWriteTokens") or 0,
               0]
        if any(acc):
            out[os.path.splitext(os.path.basename(f))[0]] = acc
    return out


def _snapshot_dsh(config: dict | None = None) -> dict:
    return _dsh_sessions(config)


def _snapshot_codex(config: dict | None = None) -> dict:
    root = path_for("codex", config)
    if not os.path.isdir(root):
        return {}
    out: dict[str, list[int]] = {}
    for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        # Same cached scan as collect_codex, so the two passes in one poll read
        # each rollout log at most once between them.
        latest = _codex_file_buckets(f)
        if not latest:
            continue
        out[os.path.basename(f)] = [
            latest.get("input_tokens") or 0,
            latest.get("output_tokens") or 0,
            latest.get("cached_input_tokens") or 0,
            latest.get("cache_write_input_tokens") or 0,
            latest.get("reasoning_output_tokens") or 0,
        ]
    return out


def _snapshot_claude_code(config: dict | None = None) -> dict:
    root = path_for("claude_code", config)
    if not os.path.isdir(root):
        return {}
    out: dict[str, list[int]] = {}

    def harvest(node, found):
        if isinstance(node, dict):
            usage = node.get("usage")
            if isinstance(usage, dict) and any(
                    k in usage for k in ("input_tokens", "output_tokens",
                                         "cache_read_input_tokens",
                                         "cache_creation_input_tokens")):
                found.append(usage)
            for v in node.values():
                harvest(v, found)
        elif isinstance(node, list):
            for v in node:
                harvest(v, found)

    for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        acc = [0, 0, 0, 0, 0]
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    found: list = []
                    harvest(d, found)
                    for us in found:
                        acc[0] += us.get("input_tokens") or 0
                        acc[1] += us.get("output_tokens") or 0
                        acc[2] += us.get("cache_read_input_tokens") or 0
                        acc[3] += us.get("cache_creation_input_tokens") or 0
        except OSError:
            continue
        if any(acc):
            out[os.path.basename(f)] = acc
    return out


SNAPSHOT_FNS = {
    "Hermes": _snapshot_hermes,
    "opencode": _snapshot_opencode,
    "Cline": _snapshot_cline,
    "DeepSeek Harness": _snapshot_dsh,
    "Codex": _snapshot_codex,
    "Claude Code": _snapshot_claude_code,
}


def collect_snapshots(config: dict | None = None) -> dict:
    """{source: {session_key: [input, output, cache_read, cache_write, reasoning]}}

    Capped per source: when a tool has more sessions than the cap allows, the
    most valuable ones to keep are the ones that are still moving, so anything
    beyond the cap is dropped by current magnitude, which is the best available
    proxy for recent activity without an extra timestamp column.
    """
    out: dict[str, dict] = {}
    for name, fn in SNAPSHOT_FNS.items():
        sessions = fn(config)
        if len(sessions) > MAX_SESSIONS_PER_SOURCE:
            ordered = sorted(
                sessions.items(), key=lambda kv: -sum(kv[1] or ()))
            sessions = dict(ordered[:MAX_SESSIONS_PER_SOURCE])
        out[name] = sessions
    return out


@dataclass
class Usage:
    """Token counts for one source, split by kind."""
    source: str = ""
    ok: bool = False
    error: str = ""
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0
    api_calls: int = 0
    cost_usd: float = 0.0
    cost_known: bool = False
    sessions: int = 0
    run: int = 0
    note: str = ""
    # False only when the source's store is simply not on this machine. The
    # panel hides those rows instead of showing red text, while a source that is
    # present but unreadable keeps its inline error. An error string cannot carry
    # that distinction reliably, so it is stated outright.
    installed: bool = True

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write + self.reasoning

    @property
    def fresh(self) -> int:
        """Tokens that were not served from cache - the number that maps to money."""
        return self.input + self.output + self.reasoning

    def buckets(self) -> dict:
        return {
            "input": self.input,
            "output": self.output,
            "reasoning": self.reasoning,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        d["fresh"] = self.fresh
        return d


def _ro(path: str) -> sqlite3.Connection:
    """Read-only connection. mode=ro never creates a file and never blocks the
    writer, which matters because these databases are written continuously."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


# --------------------------------------------------------------- Hermes
def collect_hermes(config: dict | None = None) -> Usage:
    u = Usage(source="Hermes")
    db = path_for("hermes", config)
    if not os.path.exists(db):
        u.error = "state.db not found"
        u.installed = False
        return u
    try:
        con = _ro(db)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        r = cur.execute("""
            SELECT COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o,
                   COALESCE(SUM(cache_read_tokens),0) cr, COALESCE(SUM(cache_write_tokens),0) cw,
                   COALESCE(SUM(reasoning_tokens),0) rs, COALESCE(SUM(api_call_count),0) calls
            FROM session_model_usage""").fetchone()
        u.input, u.output, u.cache_read, u.cache_write = r["i"], r["o"], r["cr"], r["cw"]
        u.reasoning, u.api_calls = r["rs"], r["calls"]
        # Counted on the same axis the snapshot uses - one session+model key per
        # row group - and only where tokens were actually recorded. "sessions"
        # then means "sessions this app can see usage for", which is what the
        # panel's session figure claims, and it cannot disagree with the
        # per-session snapshots that drive the per-run figure.
        u.sessions = cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT session_id, model FROM session_model_usage
                GROUP BY session_id, model
                HAVING SUM(input_tokens) + SUM(output_tokens)
                     + SUM(cache_read_tokens) + SUM(cache_write_tokens)
                     + SUM(reasoning_tokens) > 0)""").fetchone()[0]
        cost = cur.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd),0) e,"
            " COALESCE(SUM(actual_cost_usd),0) a FROM session_model_usage").fetchone()
        u.cost_usd = max(cost["e"] or 0, cost["a"] or 0)
        u.cost_known = u.cost_usd > 0
        con.close()
        u.ok = True
    except Exception as e:                              # noqa: BLE001
        u.error = f"{type(e).__name__}: {e}"
    return u


# --------------------------------------------------------------- opencode
def collect_opencode(config: dict | None = None) -> Usage:
    """session_v2 stores time_created / time_updated in MILLISECONDS."""
    u = Usage(source="opencode")
    db = path_for("opencode", config)
    if not os.path.exists(db):
        u.error = "opencode.db not found"
        u.installed = False
        return u
    try:
        con = _ro(db)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        r = cur.execute("""
            SELECT COALESCE(SUM(tokens_input),0) i, COALESCE(SUM(tokens_output),0) o,
                   COALESCE(SUM(tokens_cache_read),0) cr, COALESCE(SUM(tokens_cache_write),0) cw,
                   COALESCE(SUM(tokens_reasoning),0) rs, COALESCE(SUM(cost),0) cost
            FROM session_v2""").fetchone()
        u.input, u.output = r["i"], r["o"]
        u.cache_read, u.cache_write = r["cr"], r["cw"]
        u.reasoning, u.cost_usd = r["rs"], r["cost"] or 0
        u.cost_known = u.cost_usd > 0
        u.sessions = cur.execute("SELECT COUNT(*) FROM session_v2").fetchone()[0]
        con.close()
        u.ok = True
    except Exception as e:                              # noqa: BLE001
        u.error = f"{type(e).__name__}: {e}"
    return u


# --------------------------------------------------------------- Cline
def collect_cline(config: dict | None = None) -> Usage:
    """Per-message metrics in <session>/<session>.messages.json.

    One pass over the session directories, accumulating the lifetime totals.
    The day figure is resolved centrally by diffing per-session snapshots.
    """
    u = Usage(source="Cline")
    root = path_for("cline", config)
    if not os.path.isdir(root):
        u.error = "no ~/.cline/data/sessions"
        u.installed = False
        return u
    try:
        for d in glob.glob(os.path.join(root, "*")):
            if not os.path.isdir(d):
                continue
            s_total = 0
            for f in glob.glob(os.path.join(d, "*.messages.json")):
                try:
                    with open(f, encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                for m in data.get("messages") or []:
                    mt = m.get("metrics")
                    if not isinstance(mt, dict):
                        continue
                    u.input += mt.get("inputTokens") or 0
                    u.output += mt.get("outputTokens") or 0
                    u.cache_read += mt.get("cacheReadTokens") or 0
                    u.cache_write += mt.get("cacheWriteTokens") or 0
                    cost = mt.get("cost")
                    if cost:
                        u.cost_usd += cost
                    s_total += ((mt.get("inputTokens") or 0)
                                + (mt.get("outputTokens") or 0)
                                + (mt.get("cacheReadTokens") or 0)
                                + (mt.get("cacheWriteTokens") or 0))
            if s_total:
                u.sessions += 1
        u.cost_known = u.cost_usd > 0
        u.ok = True
    except Exception as e:                              # noqa: BLE001
        u.error = f"{type(e).__name__}: {e}"
    return u


# --------------------------------------------------------------- DeepSeek Harness
def collect_dsh(config: dict | None = None) -> Usage:
    """Sessions under ~/.dsh/storages/session_projcache/sessions/*.json.

    Every session file is rewritten each turn with its own cumulative totals, so
    the lifetime figure is the sum over sessions and the session key doubles as
    the identity used for per-run differencing. See _dsh_sessions for why the
    aggregate rollup next to the directory is not used, and for the field layout.
    """
    u = Usage(source="DeepSeek Harness")
    per_dir = os.path.join(path_for("dsh", config), "sessions")
    if not os.path.isdir(per_dir):
        u.error = "session_projcache not found"
        u.installed = False
        return u
    try:
        for acc in _dsh_sessions(config).values():
            u.input += acc[0]
            u.output += acc[1]
            u.cache_read += acc[2]
            u.cache_write += acc[3]
            u.sessions += 1
        u.ok = True
    except Exception as e:                              # noqa: BLE001
        u.error = f"{type(e).__name__}: {e}"
    return u


# --------------------------------------------------------------- Codex
def collect_codex(config: dict | None = None) -> Usage:
    """rollout-*.jsonl under ~/.codex/sessions/YYYY/MM/DD/.

    Every turn appends an event_msg whose payload.info.total_token_usage is a
    CUMULATIVE per-session total, so summing every record would multiply-count.
    Only the largest record in each file is kept.

    These logs are the largest store this app reads (tens of MiB per rollout), so
    the per-file scan is cached and resumed rather than repeated - see
    _codex_file_buckets.
    """
    u = Usage(source="Codex")
    root = path_for("codex", config)
    if not os.path.isdir(root):
        u.error = "no ~/.codex/sessions"
        u.installed = False
        return u
    try:
        for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            latest = _codex_file_buckets(f)
            if not latest:
                continue
            u.input += latest.get("input_tokens") or 0
            u.output += latest.get("output_tokens") or 0
            u.cache_read += latest.get("cached_input_tokens") or 0
            u.cache_write += latest.get("cache_write_input_tokens") or 0
            u.reasoning += latest.get("reasoning_output_tokens") or 0
            u.sessions += 1
        u.ok = True
    except Exception as e:                              # noqa: BLE001
        u.error = f"{type(e).__name__}: {e}"
    return u


# --------------------------------------------------------------- Claude Code
def collect_claude_code(config: dict | None = None) -> Usage:
    """Claude Code writes ~/.claude/projects/<slug>/<session-uuid>.jsonl.

    Each assistant turn carries message.usage with:
        input_tokens, output_tokens,
        cache_creation_input_tokens, cache_read_input_tokens

    Note the field name and the extra bucket: unlike Codex there is no
    cache_write_input_tokens, the write side is called cache_creation and lands
    in the same output bucket as the read side. "fresh" is therefore
    input + output + cache_creation - the cache READ is excluded.

    The usage object can sit at different depths (top level, under "message",
    nested in an event payload), so it is located by shape rather than by a
    fixed key path.
    """
    u = Usage(source="Claude Code")
    root = path_for("claude_code", config)
    if not os.path.isdir(root):
        u.error = "no ~/.claude/projects"
        u.installed = False
        return u

    keys = ("input_tokens", "output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens")

    def harvest(obj, found: list) -> None:
        """Collect every dict that looks like a usage record."""
        stack = [obj]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                u_ = o.get("usage")
                if isinstance(u_, dict) and any(k in u_ for k in keys):
                    found.append(u_)
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)

    try:
        for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            s_in = s_out = s_cc = s_cr = 0
            counted = False
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if '"usage"' not in line:
                            continue
                        try:
                            d = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        found: list = []
                        harvest(d, found)
                        if not found:
                            continue
                        counted = True
                        for u_ in found:
                            s_in += u_.get("input_tokens") or 0
                            s_out += u_.get("output_tokens") or 0
                            s_cc += u_.get("cache_creation_input_tokens") or 0
                            s_cr += u_.get("cache_read_input_tokens") or 0
            except OSError:
                continue
            # A transcript with no usage, or only zero-valued usage records,
            # holds no consumption. Counting it as a session would make the
            # panel claim to be reading a session it found nothing in, and would
            # disagree with the snapshot, which keeps only sessions with tokens.
            if not counted or not (s_in or s_out or s_cc or s_cr):
                continue
            u.input += s_in
            u.output += s_out
            u.cache_write += s_cc          # cache creation is the write side
            u.cache_read += s_cr
            u.sessions += 1
        u.ok = True
    except Exception as e:                              # noqa: BLE001
        u.error = f"{type(e).__name__}: {e}"
    return u


COLLECTORS = (collect_hermes, collect_opencode, collect_cline, collect_dsh,
              collect_codex, collect_claude_code)


def collect_all(config: dict | None = None) -> list[Usage]:
    """Collect every source, then work out what this run has spent.

    The launch baseline is captured on the first call and kept for the life of
    the process; every later call reports the difference. A first poll therefore
    reports zero, which is correct: nothing has been spent since the app opened.

    A snapshot failure must never take the panel down, so it degrades to zero
    rather than to a figure we cannot stand behind.
    """
    usage = [fn(config) for fn in COLLECTORS]
    try:
        snaps = collect_snapshots(config)
    except Exception as e:                             # noqa: BLE001
        # Must never take the panel down, but a silent fallback here hides real
        # breakage behind a plausible-looking zero, so record why.
        try:
            with open(os.path.join(_state_dir(), "..",
                                  "snapshot-error.log"),
                      "a", encoding="utf-8") as fh:
                fh.write(f"{_now_stamp()} {type(e).__name__}: {e}\n")
        except OSError:
            pass
        return usage

    if not counter_active():
        arm_counter(snaps)
        for u in usage:
            u.run = 0
        return usage

    growth = since_launch(snaps)
    for u in usage:
        u.run = growth.get(u.source, 0)
    return usage


def totals(usage: list[Usage]) -> dict:
    """Grand total across every source, plus the distribution split."""
    out = {k: 0 for k in ("input", "output", "reasoning", "cache_read", "cache_write")}
    cost = 0.0
    cost_known = False
    spent = 0
    sessions = 0
    for u in usage:
        if not u.ok:
            continue
        for k, v in u.buckets().items():
            out[k] += v
        spent += u.run
        sessions += u.sessions
        if u.cost_known:
            cost_known = True
            cost += u.cost_usd
    out["total"] = sum(out.values())
    out["fresh"] = out["input"] + out["output"] + out["reasoning"]
    out["cost_usd"] = cost
    out["cost_known"] = cost_known
    out["spent"] = spent
    out["sessions"] = sessions
    return out


if __name__ == "__main__":
    cfg = load_config()
    data = collect_all(cfg)
    for u in data:
        flag = "ok" if u.ok else f"ERR {u.error}"
        print(f"{u.source:18} {flag}")
        print(f"   in={u.input:>14,}  out={u.output:>11,}  reason={u.reasoning:>11,}"
              f"  cache_r={u.cache_read:>15,}")
        print(f"   fresh={u.fresh:>13,}  all={u.total:>15,}"
              f"  run={u.run:>13,}  sessions={u.sessions}")
        print()
    t = totals(data)
    print(f"GRAND TOTAL TOKENS: {t['total']:,}   (fresh {t['fresh']:,})")
    print(f"  spent since launch across all sources: {t['spent']:,}"
          f" over {t['sessions']} sessions")
    print("\nDISTRIBUTION:")
    for k in ("input", "output", "reasoning", "cache_read", "cache_write"):
        v = t[k]
        pct = (v / t["total"] * 100) if t["total"] else 0
        print(f"  {k:12} {v:>15,}  {pct:5.1f}%  {'#' * int(pct / 2)}")
