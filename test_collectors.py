"""Smoke tests for TokenFloatS's collectors - no GUI, no network.

    python -m pytest test_collectors.py     # if pytest is available
    python test_collectors.py               # standalone, prints a summary

These use temp fixtures, never the developer's real databases, so running the
suite cannot alter a real install.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collectors as C                                        # noqa: E402


def _today(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def build_hermes_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE session_model_usage (
            session_id TEXT, model TEXT, billing_provider TEXT, task TEXT,
            api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER,
            reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL,
            first_seen REAL, last_seen REAL);
        CREATE TABLE sessions (id TEXT, title TEXT);
        INSERT INTO sessions VALUES ('s1','t1');
    """)
    now = time.time()
    con.execute("""INSERT INTO session_model_usage VALUES
        ('s1','m','p','',10,100,20,300,5,7,0.0,0.0,?,?)""", (now, now))
    con.commit()
    con.close()


def build_opencode_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE session_v2 (
            id TEXT, cost REAL, tokens_input INTEGER, tokens_output INTEGER,
            tokens_reasoning INTEGER, tokens_cache_read INTEGER,
            tokens_cache_write INTEGER, time_created INTEGER, time_updated INTEGER);
    """)
    ms = int(time.time() * 1000)            # milliseconds on purpose
    con.execute("""INSERT INTO session_v2 VALUES
        ('a',0.5,11,12,13,14,15,?,?)""", (ms, ms))
    con.commit()
    con.close()


def build_cline_tree(root: str) -> None:
    d = os.path.join(root, "sess1")
    os.makedirs(d, exist_ok=True)
    payload = {"messages": [{"metrics": {"inputTokens": 1, "outputTokens": 2,
                                         "cacheReadTokens": 3, "cacheWriteTokens": 4,
                                         "cost": 0.25},
                             "ts": int(time.time() * 1000)}]}
    with open(os.path.join(d, "sess1.messages.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def build_dsh_tree(root: str) -> None:
    os.makedirs(os.path.join(root, "sessions"), exist_ok=True)
    totals = {"uncachedInputTokens": 21, "outputTokens": 22,
              "cacheReadTokens": 23, "cacheWriteTokens": 24}
    rec = {"version": 1, "record": {"identity": {"createdAt": 1},
                                    "rows": {"tokenUsage": {"ver": 1, "seq": 1,
                                                           "val": {"totals": totals}}}}}
    with open(os.path.join(root, "sessions", "s1.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh)


def build_codex_tree(root: str) -> None:
    os.makedirs(os.path.join(root, "2026", "01", "01"), exist_ok=True)
    f = os.path.join(root, "2026", "01", "01", "rollout-2026-01-01T00-00-00-x.jsonl")
    with open(f, "w", encoding="utf-8") as fh:
        # cumulative totals: the collector must keep only the LAST/largest
        for total in (100, 200, 300):
            line = {
                "timestamp": f"2026-01-01T00:00:0{total // 100}.000Z",
                "type": "event_msg",
                "payload": {"type": "token_count", "info": {"total_token_usage": {
                    "input_tokens": total, "cached_input_tokens": total // 2,
                    "cache_write_input_tokens": 0, "output_tokens": total // 10,
                    "reasoning_output_tokens": 0, "total_tokens": total * 3 // 2}}},
            }
            fh.write(json.dumps(line) + "\n")


def build_claude_tree(root: str) -> None:
    """usage sits under message.usage; cache creation is a separate bucket."""
    d = os.path.join(root, "C--Users-x")
    os.makedirs(d, exist_ok=True)
    lines = []
    for i, (inp, out, cc, cr, ts) in enumerate([
        (100, 20, 30, 400, "2026-01-01T00:00:00.000Z"),
        (50, 10, 5, 200, "2026-01-02T00:00:00.000Z"),
    ]):
        lines.append(json.dumps({
            "type": "assistant", "timestamp": ts,
            "message": {"model": "claude", "usage": {
                "input_tokens": inp, "output_tokens": out,
                "cache_creation_input_tokens": cc,
                "cache_read_input_tokens": cr}}}))
    # a non-usage line that must be ignored, and a nested usage to prove the
    # shape-based lookup does not depend on a fixed key path
    lines.append(json.dumps({"type": "user", "content": "hi"}))
    lines.append(json.dumps({
        "type": "event", "timestamp": "2026-01-02T01:00:00.000Z",
        "payload": {"usage": {"input_tokens": 7, "output_tokens": 3,
                               "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 11}}}))
    with open(os.path.join(d, "s1.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def build_all(tmp: str) -> dict:
    hermes = os.path.join(tmp, "hermes.db")
    opencode = os.path.join(tmp, "opencode.db")
    cline = os.path.join(tmp, "cline")
    dsh = os.path.join(tmp, "dsh")
    codex = os.path.join(tmp, "codex")
    claude = os.path.join(tmp, "claude")
    build_hermes_db(hermes)
    build_opencode_db(opencode)
    build_cline_tree(cline)
    build_dsh_tree(dsh)
    build_codex_tree(codex)
    build_claude_tree(claude)
    return {"hermes": hermes, "opencode": opencode, "cline": cline,
            "dsh": dsh, "codex": codex, "claude_code": claude}


def main() -> int:
    failures: list[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")
            print(f"  FAIL {label}: got {got!r}, want {want!r}")
        else:
            print(f"  ok   {label} = {got!r}")

    with tempfile.TemporaryDirectory(prefix="tokenfloats-test-") as tmp:
        cfg = {"paths": build_all(tmp)}

        h = C.collect_hermes(cfg)
        check("hermes ok", h.ok, True)
        check("hermes input", h.input, 100)
        check("hermes sessions", h.sessions, 1)

        o = C.collect_opencode(cfg)
        check("opencode ok", o.ok, True)
        check("opencode cost_known", o.cost_known, True)

        c = C.collect_cline(cfg)
        check("cline ok", c.ok, True)
        check("cline input", c.input, 1)
        check("cline cost", round(c.cost_usd, 2), 0.25)

        d = C.collect_dsh(cfg)
        check("dsh ok", d.ok, True)
        check("dsh uncached->input", d.input, 21)
        check("dsh output", d.output, 22)

        x = C.collect_codex(cfg)
        check("codex ok", x.ok, True)
        # cumulative records 100/200/300: input must be 300, NOT 600
        check("codex input (no double count)", x.input, 300)
        check("codex cache_read", x.cache_read, 150)
        check("codex output", x.output, 30)

        cc = C.collect_claude_code(cfg)
        check("claude ok", cc.ok, True)
        # 100+50+7 input, 20+10+3 output, 30+5 creation, 400+200+11 read
        check("claude input", cc.input, 157)
        check("claude output", cc.output, 33)
        check("claude cache_write (creation)", cc.cache_write, 35)
        check("claude cache_read", cc.cache_read, 611)
        check("claude sessions", cc.sessions, 1)
        # a line with no usage must not create a session of its own
        check("claude ignores non-usage lines", cc.sessions, 1)

        data = C.collect_all(cfg)
        t = C.totals(data)
        check("grand total = sum of parts",
              t["total"], t["input"] + t["output"] + t["reasoning"]
              + t["cache_read"] + t["cache_write"])
        check("fresh excludes cache", t["fresh"],
              t["input"] + t["output"] + t["reasoning"])

        # a missing source must degrade, not raise
        empty = {"paths": {k: os.path.join(tmp, "nope", k) for k in cfg["paths"]}}
        for u in C.collect_all(empty):
            check(f"missing {u.source} degrades", u.ok, False)
            check(f"missing {u.source} has error", bool(u.error), True)

        # detection: every configured fixture path exists, and the second
        # candidate for opencode is reported as also-present rather than lost
        found, missing = C.detect_all(cfg)
        # detect reports the names the panel shows; cfg is keyed by the short
        # name, so compare through that mapping
        check("detect finds all six", sorted(found),
              sorted(C.display_name(k) for k in cfg["paths"]))
        check("detect has no missing", sorted(missing), [])

        override = dict(cfg)
        override["paths"] = {**cfg["paths"], "opencode": os.path.join(tmp, "nope", "oc.db")}
        f2, m2 = C.detect_all(override)
        check("detect honours an override that misses", "opencode" in m2, True)
        check("detect override not in found", "opencode" in f2, False)

        empty2 = {"paths": {k: os.path.join(tmp, "nope", k) for k in cfg["paths"]}}
        f3, m3 = C.detect_all(empty2)
        check("detect all-missing", (len(f3), len(m3)), (0, len(cfg["paths"])))

        # ---- since-launch accounting: the figure is growth since this process
        # started, never a cumulative total that was already counted before.
        base = {"Hermes": {"s1": [1000, 100, 50_000, 0, 10],
                           "s2": [2000, 200, 60_000, 0, 20]}}
        grown = {"Hermes": {"s1": [1100, 100, 55_000, 0, 10],
                            "s2": [2000, 200, 60_000, 0, 20]}}
        check("growth counts the difference only",
              C.session_growth(grown, base)["Hermes"], 5100)
        check("growth with no change is zero",
              C.session_growth(base, base)["Hermes"], 0)
        check("growth without a launch baseline is zero",
              C.session_growth(base, {})["Hermes"], 0)
        check("growth ignores a session created after launch",
              C.session_growth({"Hermes": {"s1": [1, 0, 0, 0, 0],
                                           "new": [7, 0, 0, 0, 0]}},
                               {"Hermes": {"s1": [1, 0, 0, 0, 0]}})["Hermes"], 0)
        check("growth on a reset session counts its current value",
              C.session_growth({"Hermes": {"s1": [10, 0, 0, 0, 0]}},
                               base)["Hermes"], 10)

        state: dict = {}
        C.arm_counter({"Hermes": {"s1": [1000, 0, 10_000, 0, 0]}})
        check("the counter is armed", C.counter_active(), True)
        t1 = C.since_launch({"Hermes": {"s1": [1000, 0, 10_000, 0, 0]}})
        check("the launch poll spends nothing", t1.get("Hermes", 0), 0)
        t2 = C.since_launch({"Hermes": {"s1": [1500, 0, 10_000, 0, 0]}})
        check("growth is counted", t2["Hermes"], 500)
        t3 = C.since_launch({"Hermes": {"s1": [1500, 0, 12_000, 0, 0]}})
        check("the figure is cumulative, never summed twice", t3["Hermes"], 2500)
        t4 = C.since_launch({"Hermes": {"s1": [1500, 0, 12_000, 0, 0]}})
        check("an unchanged poll does not move it", t4["Hermes"], 2500)

        runs = C.recent_runs(1)
        check("a run record exists", len(runs), 1)
        check("the record carries date and time",
              len(runs[0]["start"]) == 16 and runs[0]["start"][4] == "-", True)
        check("the record holds this run's figure", runs[0]["total"], 2500)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        for f in failures:
            print("  -", f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
