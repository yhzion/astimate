"""Post-sweep deep analysis. Reads results/runs.csv and prints:

  1. Done-controlled comparison (status=done runs only) — isolates whether
     C2's token savings come from efficiency or from giving up early.
  2. Status breakdown by (condition, type) — where do cap_steps/cap_tokens hit?
  3. Per-type done-controlled token comparison.
  4. Type E failure sampling — show the agent's answers for the loc-slicing
     task, where C2 unexpectedly regressed.

Usage:  python -m experiment.harness.analyze [--csv results/runs.csv]
"""
from __future__ import annotations
import argparse
import csv
import json
import statistics
from collections import defaultdict, Counter
from pathlib import Path

from . import config

TYPES = ["A", "B", "C", "D", "E"]
CONDS = ["C0", "C1", "C2"]


def _load(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["warmup"] == "0"]


def _toks(rows): return [int(r["total_tokens"]) for r in rows]
def _accs(rows): return [float(r["acc_binary"]) for r in rows]


def _trim(xs, frac=0.2):
    if not xs:
        return 0
    xs = sorted(xs)
    k = int(len(xs) * frac)
    core = xs[k:len(xs) - k] if k > 0 else xs
    return sum(core) / len(core) if core else sum(xs) / len(xs)


def _pct(xs, p):
    if not xs:
        return 0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def _stats(rows):
    if not rows:
        return None
    t = _toks(rows)
    return dict(
        n=len(rows),
        mean=sum(t) / len(t),
        trim=_trim(t),
        med=statistics.median(t),
        p10=_pct(t, 10), p90=_pct(t, 90),
        acc=sum(_accs(rows)) / len(rows),
    )


def _row(label, s):
    if not s:
        print(f"  {label}: (no data)")
        return
    print(f"  {label}: n={s['n']:>3} | mean={s['mean']:>7.0f} trim={s['trim']:>7.0f} "
          f"med={s['med']:>7.0f} p10={s['p10']:>6.0f} p90={s['p90']:>7.0f} | acc={s['acc']:.2f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(config.RESULTS / "runs.csv"))
    args = ap.parse_args(argv)
    rows = _load(Path(args.csv))

    # ------------------------------------------------------------------ #
    print("=" * 78)
    print("1. STATUS BREAKDOWN by (condition, type)")
    print("=" * 78)
    by = defaultdict(lambda: Counter())
    for r in rows:
        by[(r["condition"], r["type"])][r["status"]] += 1
    print(f"  {'cond/type':<12} {'done':>5} {'cap_steps':>10} {'cap_tok':>9} {'text':>5} {'err':>4}  total")
    for cond in CONDS:
        for tp in TYPES:
            c = by[(cond, tp)]
            tot = sum(c.values())
            if tot == 0:
                continue
            print(f"  {cond} type {tp:<3}  {c['done']:>5} {c['cap_steps']:>10} "
                  f"{c['cap_tokens']:>9} {c['text_no_done']:>5} {c['error']:>4}  {tot:>5}")

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("2. DONE-CONTROLLED COMPARISON (status=done runs only)")
    print("   Question: with 'gave up' runs removed, is C2 still cheaper at equal accuracy?")
    print("=" * 78)
    done = [r for r in rows if r["status"] == "done"]
    print(f"\n  All done runs (all types):")
    for cond in CONDS:
        _row(cond, _stats([r for r in done if r["condition"] == cond]))

    print(f"\n  Done runs, Type C+D (the H6 gap):")
    for cond in CONDS:
        s = _stats([r for r in done if r["condition"] == cond and r["type"] in ("C", "D")])
        _row(cond, s)

    print(f"\n  Done runs, per type:")
    for tp in TYPES:
        print(f"\n  --- Type {tp} (done only) ---")
        for cond in CONDS:
            _row(cond, _stats([r for r in done if r["condition"] == cond and r["type"] == tp]))

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("3. TYPE E FAILURE SAMPLING (loc-slicing, where C2 regressed)")
    print("=" * 78)
    e = [r for r in rows if r["type"] == "E"]
    print(f"\n  Type E status by condition:")
    for cond in CONDS:
        c = Counter(r["status"] for r in e if r["condition"] == cond)
        toks = _toks([r for r in e if r["condition"] == cond])
        accs = _accs([r for r in e if r["condition"] == cond])
        print(f"    {cond}: n={len(c)} acc={sum(accs)/len(accs):.2f} "
              f"trim_tok={_trim(toks):.0f} med_tok={statistics.median(toks):.0f} | {dict(c)}")

    print(f"\n  Type E agent answers (first 3 per condition):")
    for cond in CONDS:
        print(f"\n  === {cond} ===")
        samples = [r for r in e if r["condition"] == cond][:3]
        for r in samples:
            ans = r["answer_json"]
            print(f"    [{r['task_id']} r{r['repeat']}] status={r['status']} "
                  f"acc={r['acc_binary']} tok={r['total_tokens']} steps={r['steps']}")
            print(f"       tools: {r['tool_calls_json'][:120]}")
            print(f"       answer: {ans[:240]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
