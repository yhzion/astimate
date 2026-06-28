"""Load experiment/tasks.toml into typed task objects."""
from __future__ import annotations
import tomllib
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Expected:
    symbol: str
    file: str
    loc: tuple[int, int]
    kind: str
    note: str = ""


@dataclass(frozen=True)
class Task:
    id: str
    type: str
    question: str
    expected: list[Expected]


def load_tasks() -> list[Task]:
    with open(config.TASKS_TOML, "rb") as f:
        data = tomllib.load(f)
    tasks: list[Task] = []
    for t in data["task"]:
        exp = [
            Expected(
                symbol=e["symbol"],
                file=e["file"],
                loc=(int(e["loc"][0]), int(e["loc"][1])),
                kind=e.get("kind", ""),
                note=e.get("note", ""),
            )
            for e in t["expected"]
        ]
        tasks.append(Task(id=t["id"], type=t["type"], question=t["question"], expected=exp))
    return tasks
