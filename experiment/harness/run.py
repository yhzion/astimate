"""Orchestrator: run (task x condition x repeat), write results CSV + summary.

Usage:
    python -m experiment.harness.run --conditions C0 C1 C2 --repeats 5
    python -m experiment.harness.run --tasks A1 C1 --conditions C0 --repeats 1  # smoke
"""
from __future__ import annotations
import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

from . import config, agent, judge
from .fixtures import load_tasks

CONDITIONS = ["C0", "C1", "C2"]


def _warmup_skip(repeat: int) -> bool:
    return repeat == 0  # discard first run per (task, condition)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", nargs="+", default=CONDITIONS)
    ap.add_argument("--repeats", type=int, default=5,
                    help="number of measured repeats per (task,condition); +1 warmup each")
    ap.add_argument("--tasks", nargs="+", default=None, help="subset of task ids")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--out", default=str(config.RESULTS / "runs.csv"))
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

    rng = random.Random(args.seed)
    plan: list[tuple] = []
    for t in tasks:
        for cond in args.conditions:
            for r in range(args.repeats + 1):  # +1 warmup
                plan.append((t, cond, r))
    rng.shuffle(plan)

    total = len(plan)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "task_id", "type", "condition", "repeat", "warmup", "status",
            "acc_binary", "acc_partial", "n_expected", "n_found",
            "prompt_tokens", "completion_tokens", "total_tokens",
            "steps", "tool_calls_json", "wall_clock", "error", "answer_json",
        ])

        for i, (task, cond, rep) in enumerate(plan, 1):
            warmup = _warmup_skip(rep)
            tag = "WARMUP" if warmup else "RUN"
            print(f"[{i}/{total}] {tag} {task.id} {cond} r{rep} ...", flush=True)
            t0 = time.time()
            res = agent.run(task, cond, rep)
            dt = time.time() - t0
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
                  f"| tok={res.total_tokens} | steps={res.steps} | {dt:.1f}s",
                  flush=True)

    print(f"\nDone. Results -> {out_path}")
    _summary(out_path)
    return 0


def _summary(csv_path: Path) -> None:
    """Print per-condition aggregates over measured runs (warmup excluded)."""
    by_cond: dict[str, list[dict]] = {}
    by_cond_type: dict[tuple[str, str], list[dict]] = {}
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["warmup"] == "1":
                continue
            by_cond.setdefault(row["condition"], []).append(row)
            by_cond_type.setdefault((row["condition"], row["type"]), []).append(row)

    def agg(rows):
        n = len(rows)
        if not n:
            return None
        toks = [int(r["total_tokens"]) for r in rows]
        acc = [float(r["acc_binary"]) for r in rows]
        done = sum(1 for r in rows if r["status"] == "done")
        return {
            "n": n, "mean_tok": sum(toks)/n, "median_tok": sorted(toks)[n//2],
            "acc": sum(acc)/n, "done_rate": done/n,
        }

    print("\n=== Per-condition (all task types) ===")
    for cond in CONDITIONS:
        a = agg(by_cond.get(cond, []))
        if a:
            print(f"  {cond}: n={a['n']} | mean_tok={a['mean_tok']:.0f} "
                  f"median_tok={a['median_tok']} | acc={a['acc']:.2f} done={a['done_rate']:.2f}")

    print("\n=== Per-condition x type (decision-relevant: C, D = the gap) ===")
    for cond in CONDITIONS:
        for tp in ["A", "B", "C", "D", "E"]:
            a = agg(by_cond_type.get((cond, tp), []))
            if a:
                print(f"  {cond} type {tp}: n={a['n']} mean_tok={a['mean_tok']:.0f} "
                      f"acc={a['acc']:.2f}")

    # H6 decision view: Type C+D
    print("\n=== H6 decision view (Type C+D, the LSP/RAG gap) ===")
    for cond in CONDITIONS:
        rows = [r for (c, t), rs in by_cond_type.items() if c == cond and t in ("C", "D") for r in rs]
        a = agg(rows)
        if a:
            print(f"  {cond}: mean_tok={a['mean_tok']:.0f} median_tok={a['median_tok']} "
                  f"acc={a['acc']:.2f}")


if __name__ == "__main__":
    raise SystemExit(main())
