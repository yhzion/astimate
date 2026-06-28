"""The agent loop: drive the model through tool calls until it calls `done`
or hits a step/token cap. Identical across conditions except the tool set."""
from __future__ import annotations
import time
from dataclasses import dataclass, field

from . import config, llm, tools
from .fixtures import Task


@dataclass
class RunResult:
    task_id: str
    condition: str
    repeat: int
    status: str                      # done | cap_steps | cap_tokens | text_no_done | error
    answer: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    steps: int = 0
    tool_calls: list[str] = field(default_factory=list)
    wall_clock: float = 0.0
    error: str = ""


def _system_prompt(condition: str) -> str:
    extra = {
        "C0": ("You have NO symbol index. Use read_file and grep to navigate."),
        "C1": ("You have a RAG retrieval tool `rag_search` over 50-line code chunks, "
               "plus read_file and grep."),
        "C2": ("You have an astimate symbol index tool `ast_search` that returns symbols "
               "(with signature, references, precise line range, purpose and tags), "
               "plus read_file and grep. Prefer ast_search to locate symbols, then read_file "
               "only for the specific lines you need."),
    }[condition]
    return (
        "You are a code-navigation agent answering questions about a Rust codebase "
        "(the ripgrep library crates) located under ripgrep_crates/. "
        + extra +
        "\n\nIMPORTANT: Do NOT answer from memory or hallucinate file paths. You MUST "
        "investigate the repository with the tools first; every symbol, file path, and "
        "line range in your final answer must come from tool results you actually observed. "
        "Your goal: identify the exact symbol(s) the question asks about, including their "
        "file and line range. You MUST finish by calling the `done` tool with the full list "
        "of requested symbols. Do NOT answer in prose — any prose answer is invalid and will "
        "be discarded. Be efficient: minimize tool calls."
    )


def _user_prompt(task: Task) -> str:
    return f"Question: {task.question}\n\nAnswer via the `done` tool."


def _tool_sig(name: str, args: dict) -> str:
    """Stable signature of a tool call for loop detection."""
    norm = []
    for k in sorted(args):
        v = str(args[k]).strip()
        norm.append(f"{k}={v[:60]}")
    return f"{name}({','.join(norm)})"


def run(task: Task, condition: str, repeat: int) -> RunResult:
    res = RunResult(task_id=task.id, condition=condition, repeat=repeat, status="error")
    t0 = time.time()
    schemas = tools.schemas_for(condition)
    primary = {"C0": "grep", "C1": "rag_search", "C2": "ast_search"}[condition]
    messages = [
        {"role": "system", "content": _system_prompt(condition)},
        {"role": "user", "content": _user_prompt(task)},
    ]
    text_nudges = 0
    loop_nudges = 0
    recent_sigs: list[str] = []          # last N tool-call signatures (loop detector)

    try:
        for step in range(config.MAX_STEPS):
            res.steps = step + 1
            # Turn 1: force the condition's primary search tool so the agent
            # consults the index instead of answering from memory.
            force = primary if step == 0 else None
            tr = llm.chat(messages, tools=schemas, thinking=False, force_tool=force)
            res.prompt_tokens += tr.prompt_tokens
            res.completion_tokens += tr.completion_tokens
            res.total_tokens = res.prompt_tokens + res.completion_tokens

            if res.total_tokens > config.MAX_TOTAL_TOKENS:
                res.status = "cap_tokens"
                break

            if not tr.tool_calls:
                # No tool call: text answer or stalled. Nudge before forcing.
                content = (tr.content or "").strip()
                if not content:
                    content = ""
                if text_nudges >= config.MAX_TEXT_NUDGES:
                    res.status = "text_no_done"
                    break
                text_nudges += 1
                messages.append({"role": "assistant", "content": tr.content or ""})
                nudge = (
                    "Prose answers are discarded. Call the `done` tool now. In its `answer` "
                    "array, list EACH symbol with: 'symbol' (fully-qualified path), 'file' "
                    "(exact repo-relative path such as 'ripgrep_crates/...'), 'loc_start', "
                    "'loc_end' — copy the exact file path and line numbers from the tool "
                    "results you saw."
                )
                if text_nudges == 1:
                    messages.append({"role": "user", "content": nudge})
                    continue
                # 2nd time: force the done call as a last resort.
                messages.append({"role": "user", "content": nudge})
                forced = llm.chat(messages, tools=schemas, thinking=False, force_tool="done")
                res.prompt_tokens += forced.prompt_tokens
                res.completion_tokens += forced.completion_tokens
                res.total_tokens = res.prompt_tokens + res.completion_tokens
                tr = forced
                if not tr.tool_calls:
                    res.status = "text_no_done"
                    break

            assistant_msg = {"role": "assistant", "content": tr.content, "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in tr.tool_calls
            ]}
            messages.append(assistant_msg)

            done_called = False
            loop_break = False
            for tc in tr.tool_calls:
                res.tool_calls.append(tc["name"])
                args = tools.parse_args(tc["arguments"])

                # --- loop detection: same call signature 3x within last 4 calls ---
                sig = _tool_sig(tc["name"], args)
                recent_sigs.append(sig)
                window = recent_sigs[-4:]
                if len(window) >= 3 and window.count(sig) >= 3 and loop_nudges == 0:
                    loop_nudges += 1
                    messages.append({
                        "role": "user",
                        "content": (
                            "You are repeating the same tool call. Stop searching and submit "
                            "your best answer NOW by calling the `done` tool with the symbols, "
                            "files and line ranges you have found so far."
                        ),
                    })
                    loop_break = True
                    break

                try:
                    out = tools.dispatch(tc["name"], args)
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "name": tc["name"], "content": out,
                    })
                except tools.DoneSignal as d:
                    res.answer = d.answer
                    res.status = "done"
                    done_called = True
                    break
            if done_called:
                break
            if loop_break:
                # Injected a loop nudge; continue the loop (next turn should call done).
                continue
        else:
            res.status = "cap_steps"
    except llm.LLMError as e:
        res.status = "error"
        res.error = str(e)
    except Exception as e:  # noqa: BLE001
        res.status = "error"
        res.error = f"{type(e).__name__}: {e}"

    res.wall_clock = time.time() - t0
    return res
