"""Score an agent answer against frozen ground truth.

Match rule (per expected symbol):
  - symbol match: agent's symbol path is a suffix-subsequence of the expected
    path (or vice versa) — tolerates missing qualifiers.
  - file match: normalized basenames/relative paths agree.
  - loc match: intersection / expected_length >= 0.5  (captured the core location).
"""
from __future__ import annotations
from pathlib import PurePosixPath

from .fixtures import Task


def _norm_segs(symbol: str) -> list[str]:
    return [s.lower() for s in symbol.split("::") if s.strip()]


def _symbol_match(agent_sym: str, exp_sym: str) -> bool:
    a, e = _norm_segs(agent_sym), _norm_segs(exp_sym)
    if not a or not e:
        return False
    # agent is a suffix of expected, or expected is a suffix of agent
    def suffix_of(short, long):
        return len(short) <= len(long) and long[-len(short):] == short
    return suffix_of(a, e) or suffix_of(e, a)


def _file_match(agent_file: str, exp_file: str) -> bool:
    af = agent_file.replace("\\", "/").strip()
    ef = exp_file.replace("\\", "/").strip()
    if af == ef:
        return True
    # basename match (and parent dir if present)
    ab, eb = PurePosixPath(af).name, PurePosixPath(ef).name
    if ab != eb:
        return False
    ap = PurePosixPath(af).parent.name
    ep = PurePosixPath(ef).parent.name
    return ap == ep or not ap or not ep


def _loc_overlap(exp_loc, a_start, a_end) -> float:
    es, ee = exp_loc
    a_start, a_end = int(a_start), int(a_end)
    if a_end < a_start:
        a_start, a_end = a_end, a_start
    inter = max(0, min(ee, a_end) - max(es, a_start) + 1)
    exp_len = ee - es + 1
    return inter / exp_len if exp_len else 0.0


def score(task: Task, answer) -> dict:
    """Return {acc_binary, acc_partial, details}. Tolerates malformed answers."""
    # Normalize: unwrap {answer: [...]}, drop non-dict items.
    if isinstance(answer, dict) and "answer" in answer:
        answer = answer["answer"]
    if not answer or not isinstance(answer, list):
        return {"acc_binary": 0.0, "acc_partial": 0.0, "details": []}
    answer = [a for a in answer if isinstance(a, dict)]
    if not answer:
        return {"acc_binary": 0.0, "acc_partial": 0.0, "details": []}
    found = 0
    details = []
    for exp in task.expected:
        ok = False
        for a in answer:
            try:
                loc_ov = _loc_overlap(exp.loc, a["loc_start"], a["loc_end"])
            except (KeyError, TypeError, ValueError):
                loc_ov = 0.0
            sym_ok = _symbol_match(str(a.get("symbol", "")), exp.symbol)
            file_ok = _file_match(str(a.get("file", "")), exp.file)
            if sym_ok and file_ok and loc_ov >= 0.5:
                ok = True
                break
        details.append({"expected": exp.symbol, "found": ok})
        if ok:
            found += 1
    partial = found / len(task.expected)
    return {
        "acc_binary": 1.0 if found == len(task.expected) else 0.0,
        "acc_partial": partial,
        "details": details,
    }
