"""Central configuration: paths, model, env loading. No third-party deps."""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # astimate/
HARNESS = ROOT / "experiment" / "harness"
FIXTURES = ROOT / "experiment" / "fixtures"
REPO_ROOT = FIXTURES                              # what the agent treats as repo root
FIXTURE_ROOT = FIXTURES / "ripgrep_crates"          # the actual code subtree
TASKS_TOML = ROOT / "experiment" / "tasks.toml"
DATA = HARNESS / "data"
ASTIMATE_ROOT = ROOT / "experiment" / ".astimate"   # C2 sidecar mirror of FIXTURE_ROOT
RAG_INDEX = DATA / "rag_index.json"
MEANING_CSV = HARNESS / "meaning.csv"
MEANING_GEN_CACHE = DATA / "meaning_gen.json"
RESULTS = ROOT / "results"


def _load_dotenv() -> None:
    """Minimal .env loader. Does NOT override existing env vars."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_dotenv()

ENDPOINT = os.environ.get("ASTIMATE_LLM_ENDPOINT", "https://llm-proxy.datamaker.io")
API_KEY = os.environ.get("ASTIMATE_LLM_API_KEY", "")
MODEL = os.environ.get("ASTIMATE_LLM_MODEL", "bunker-flash")
# Build chat URL. If the endpoint already ends in a version segment
# (e.g. .../paas/v4), append /chat/completions directly; otherwise insert
# /v1 (OpenAI convention, used by e.g. vLLM proxies ending in a bare host).
import re as _re
_ep = ENDPOINT.rstrip("/")
CHAT_URL = (_ep + "/chat/completions") if _re.search(r"/v\d+$", _ep) else (_ep + "/v1/chat/completions")


# Thinking control is model-specific:
#   - vLLM-backed Qwen (bunker-flash): chat_template_kwargs.enable_thinking
#   - z.ai GLM-5.x: reasoning_effort ("minimal" disables reasoning cleanly)
_THINKING_OFF = {
    "bunker-flash": {"chat_template_kwargs": {"enable_thinking": False}},
    "glm-5.2": {"reasoning_effort": "minimal"},
    "glm-5.1": {"reasoning_effort": "minimal"},
    "glm-5": {"reasoning_effort": "minimal"},
}


def thinking_extra(thinking: bool) -> dict:
    """Extra payload fields for the configured model's thinking mode.
    thinking=False -> reasoning OFF (clean token signal for H6).
    thinking=True  -> model default (omitted)."""
    if thinking:
        return {}
    return _THINKING_OFF.get(MODEL, {})

# Agent loop limits (thinking OFF keeps each turn cheap)
MAX_STEPS = 20
MAX_TOTAL_TOKENS = 120_000
PER_TURN_MAX_TOKENS = 600
MAX_TEXT_NUDGES = 2
TEMPERATURE = 0
REQUEST_TIMEOUT = 90

for p in (DATA, RESULTS, ASTIMATE_ROOT):
    p.mkdir(parents=True, exist_ok=True)
