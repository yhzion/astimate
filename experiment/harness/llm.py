"""OpenAI-compatible chat client built on stdlib urllib.

Designed for the vLLM proxy (bunker-flash). Thinking is disabled via
chat_template_kwargs.enable_thinking=false to keep the token signal clean
(reasoning tokens would confound the H6 retrieval metric).
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field

from . import config


@dataclass
class TurnResult:
    content: str | None
    tool_calls: list[dict]            # [{id, name, arguments(str)}]
    finish_reason: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_usage: dict = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


def _post(payload: dict, retries: int = 2) -> dict:
    data = json.dumps(payload).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            config.CHAT_URL,
            data=data,
            headers={
                "Authorization": f"Bearer {config.API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
        except json.JSONDecodeError as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise LLMError(f"LLM request failed after {retries+1} attempts: {last_err}")


def chat(messages: list[dict], tools: list[dict] | None = None,
         thinking: bool = False, force_tool: str | None = None) -> TurnResult:
    """One chat turn. Returns parsed message + token usage.
    If force_tool is set, the model is forced to call that exact function
    (used to compel `done` after a prose answer)."""
    payload: dict = {
        "model": config.MODEL,
        "messages": messages,
        "temperature": config.TEMPERATURE,
        "max_tokens": config.PER_TURN_MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    if tools:
        payload["tools"] = tools
        if force_tool:
            payload["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
        else:
            payload["tool_choice"] = "auto"

    body = _post(payload)
    choice = body["choices"][0]
    msg = choice["message"]
    usage = body.get("usage", {}) or {}

    tool_calls: list[dict] = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function", {})
        tool_calls.append({
            "id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "arguments": fn.get("arguments", "") or "",
        })

    return TurnResult(
        content=msg.get("content"),
        tool_calls=tool_calls,
        finish_reason=choice.get("finish_reason", ""),
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        raw_usage=usage,
    )
