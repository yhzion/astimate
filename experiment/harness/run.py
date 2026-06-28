"""Orchestrator: run (task x condition x repeat), write results CSV + summary.

Resumable: every completed run is appended+flushed; a `--resume` flag skips
runs already present in the output CSV (keyed on task_id|condition|repeat).

Usage:
    python -m experiment.harness.run --conditions C0 C1 C2 --repeats 5
    python -m experiment.harness.run --resume                # continue after a crash
    python -m experiment.harness.run --tasks A1 C1 --conditions C0 --repeats 1  # smoke
"""
from __future__ import annotations
import argparse
import csv
import json
import random
import statistics
import sys
import time
from pathlib import Path

from . import config, agent, judge
from .fixtures import load_tasks

CONDITIONS = ["C0", "C1", "C2"]

HEADER = [
    "task_id", "type", "condition", "repeat", "warmup", "status",
    "acc_binary", "acc_partial", "n_expected", "n_found",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "steps", "tool_calls_json", "wall_clock", "error", "answer_json",
]


def _warmup_skip(repeat: int) -> bool:
    return repeat == 0  # discard first run per (task, condition)


def _completed_keys(csv_path: Path) -> set[str]:
    """Keys of runs already in the CSV (warmup-aware). Used for --resume."""
    keys: set[str] = set()
    if not csv_path.exists():
        return keys
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            wu = "W" if row["warmup"] == "1" else "R"
            keys.add(f"{row['task_id']}|{row['condition']}|{row['repeat']}|{wu}")
    return keys


def _key(task_id: str, cond: str, rep: int, warmup: bool) -> str:
    return f"{task_id}|{cond}|{rep}|{'W' if warmup else 'R'}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", nargs="+", default=CONDITIONS)
    ap.add_argument("--repeats", type=int, default=5,
                    help="number of measured repeats per (task,condition); +1 warmup each")
    ap.add_argument("--tasks", nargs="+", default=None, help="subset of task ids")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--out", default=str(config.RESULTS / "runs.csv"))
    ap.add_argument("--resume", action="store_true",
                    help="skip runs already present in --out (resume after crash)")
    ap.add_argument("--restart", action="store_true",
                    help="overwrite --out instead of resuming")
    args = ap.parse_args(argv)

    if not config.API_KEY:
        print("ERROR: ASTIMATE_LLM_API_KEY not set (check .env)", file=sys.stderr)
        return 2

    tasks = load_tasks()
    if args.tasks:
        wanted = set(args.tasks)
        tasks = [t for t in tasks if t.id in wanted]
        missing = wanted - {t.id for t in tasks}
        if missing:
            print(f"WARN: unknown task ids ignored: {sorted(missing)}", file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Header / resume bookkeeping
    resume = args.resume and not args.restart
    done_keys = _completed_keys(out_path) if resume else set()
    if out_path.exists() and resume:
        print(f"--resume: {len(done_keys)} runs already complete in {out_path}")
    new_file = (not out_path.exists()) or args.restart
    mode = "w" if new_file else "a"
    f = out_path.open(mode, newline="", encoding="utf-8")
    w = csv.writer(f)
    if new_file:
        w.writerow(HEADER)
        f.flush()

    # Build the full plan (deterministic order from seed), then filter to pending.
    rng = random.Random(args.seed)
    plan: list[tuple] = []
    for t in tasks:
        for cond in args.conditions:
            for r in range(args.repeats + 1):  # +1 warmup
                plan.append((t, cond, r))
    rng.shuffle(plan)

    pending = [(t, c, r) for (t, c, r) in plan
               if _key(t.id, c, r, _warmup_skip(r)) not in done_keys]
    total = len(pending)
    print(f"plan: {len(plan)} total | {len(plan) - total} done | {total} pending\n")

    for i, (task, cond, rep) in enumerate(pending, 1):
        warmup = _warmup_skip(rep)
        tag = "WARMUP" if warmup else "RUN"
        print(f"[{i}/{total}] {tag} {task.id} {cond} r{rep} ...", flush=True)
        res = agent.run(task, cond, rep)
        sc = judge.score(task, res.answer) if res.status == "done" else {
            "acc_binary": 0.0, "acc_partial": 0.0, "details": []}
        n_found = sum(1 for d in sc["details"] if d["found"])
        w.writerow([
            task.id, task.type, cond, rep, int(warmup), res.status,
            sc["acc_binary"], sc["acc_partial"], len(task.expected), n_found,
            res.prompt_tokens, res.completion_tokens, res.total_tokens,
            res.steps, json.dumps(res.tool_calls), round(res.wall_clock, 2),
            res.error, json.dumps(res.answer),
        ])
        f.flush()
        print(f"        -> {res.status} | acc={sc['acc_binary']:.0f} "
              f"| tok={res.total_tokens} | steps={res.steps} | {res.wall_clock:.1f}s",
              flush=True)
    f.close()

    print(f"\nDone. Results -> {out_path}")
    _summary(out_path)
    return 0


# --------------------------------------------------------------------------- #
# Summary with distribution stats (mean / trimmed-mean / median / p10 / p90)
# --------------------------------------------------------------------------- #
def _agg(rows: list[dict]) -> dict | None:
    n = len(rows)
    if not n:
        return None
    toks = sorted(int(r["total_tokens"]) for r in rows)
    acc = [float(r["acc_binary"]) for r in rows]
    done = sum(1 for r in rows if r["status"] == "done")

    def _pct(xs, p):
        if not xs:
            return 0
        k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
        return xs[k]

    def _trimmed_mean(xs, frac=0.2):
        if not xs:
            return 0
        k = int(len(xs) * frac)
        core = xs[k:len(xs) - k] if k > 0 else xs
        return sum(core) / len(core) if core else sum(xs) / len(xs)

    return {
        "n": n,
        "mean_tok": sum(toks) / n,
        "trimmed_tok": _trimmed_mean(toks),
        "median_tok": statistics.median(toks),
        "p10_tok": _pct(toks, 10),
        "p90_tok": _pct(toks, 90),
        "acc": sum(acc) / n,
        "done_rate": done / n,
    }


def _fmt(a: dict, tok_fields=("mean_tok", "trimmed_tok", "median_tok", "p10_tok", "p90_tok")) -> str:
    toks = " ".join(f"{k}={a[k]:.0f}" for k in tok_fields)
    return f"n={a['n']} | {toks} | acc={a['acc']:.2f} done={a['done_rate']:.2f}"


def _summary(csv_path: Path) -> None:
    by_cond: dict[str, list[dict]] = {}
    by_cond_type: dict[tuple[str, str], list[dict]] = {}
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["warmup"] == "1":
                continue
            by_cond.setdefault(row["condition"], []).append(row)
            by_cond_type.setdefault((row["condition"], row["type"]), []).append(row)

    print("\n=== Per-condition (all task types) ===")
    for cond in CONDITIONS:
        a = _agg(by_cond.get(cond, []))
        if a:
            print(f"  {cond}: {_fmt(a)}")

    print("\n=== Per-condition x type ===")
    for cond in CONDITIONS:
        for tp in ["A", "B", "C", "D", "E"]:
            a = _agg(by_cond_type.get((cond, tp), []))
            if a:
                print(f"  {cond} type {tp}: {_fmt(a)}")

    # H6 decision view: Type C+D (the LSP/RAG gap). Decision keys on
    # trimmed_tok / median_tok (noise-robust) at held accuracy.
    print("\n=== H6 decision view (Type C+D, the LSP/RAG gap) ===")
    decision: dict[str, dict] = {}
    for cond in CONDITIONS:
        rows = [r for (c, t), rs in by_cond_type.items()
                if c == cond and t in ("C", "D") for r in rs]
        a = _agg(rows)
        if a:
            decision[cond] = a
            print(f"  {cond}: {_fmt(a)}")

    # Apply the frozen decision rule (accuracy held + trimmed_tok threshold).
    print("\n=== H6 decision (rule: C2 trimmed_tok < 0.7 * C1 trimmed_tok, acc held) ===")
    if {"C1", "C2"} <= set(decision):
        c1, c2 = decision["C1"], decision["C2"]
        acc_held = c2["acc"] >= 0.8 * c1["acc"]            # tolerance band
        ratio = c2["trimmed_tok"] / c1["trimmed_tok"] if c1["trimmed_tok"] else float("inf")
        if not acc_held:
            verdict = "INCONCLUSIVE - C2 accuracy dropped; rerun or re-scope"
        elif ratio < 0.7:
            verdict = "H6 SUPPORTED - proceed to build"
        elif ratio < 1.0:
            verdict = "H6 WEAK - re-scope differentiation before building"
        else:
            verdict = "H6 FALSIFIED - abandon the format"
        print(f"  C2 trimmed_tok / C1 trimmed_tok = {ratio:.2f}  "
              f"(C1 acc={c1['acc']:.2f}, C2 acc={c2['acc']:.2f})")
        print(f"  VERDICT: {verdict}")
    else:
        print("  (insufficient data: need both C1 and C2 runs on Type C+D)")


if __name__ == "__main__":
    raise SystemExit(main())
