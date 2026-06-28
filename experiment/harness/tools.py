"""Tool schemas + implementations. The ONLY thing that differs across conditions
is which search tool is available (rag_search vs ast_search). read_file/grep/done
are identical in all conditions.

This isolates INDEX CONTENT (chunks vs symbols) from ACCESS MECHANICS
(same tool-call interface).
"""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path

from . import config

READ_CAP_LINES = 250          # cap read_file output to avoid token blowup
GREP_CAP_MATCHES = 40


def _arg(args: dict, *names, default=None):
    """Return the first present argument among synonym names."""
    for n in names:
        if n in args and args[n] not in (None, ""):
            return args[n]
    return default


# --------------------------------------------------------------------------- #
# Schemas (OpenAI function-calling format)
# --------------------------------------------------------------------------- #
def _schema(name: str, desc: str, params: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


SCHEMAS = {
    "read_file": _schema(
        "read_file",
        "Read a range of lines from a source file in the repository. "
        "Returns the requested lines, each prefixed with its 1-indexed line number.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative path, e.g. 'ripgrep_crates/ignore/src/walk.rs'"},
                "start_line": {"type": "integer", "description": "1-indexed start line (inclusive). Optional."},
                "end_line": {"type": "integer", "description": "1-indexed end line (inclusive). Optional."},
            },
            "required": ["path"],
        },
    ),
    "grep": _schema(
        "grep",
        "Search the repository for a regex pattern (ripgrep syntax). "
        "Returns matching lines as 'path:line:content' (capped).",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string", "description": "Optional file glob filter, e.g. '*.rs'."},
            },
            "required": ["pattern"],
        },
    ),
    "rag_search": _schema(
        "rag_search",
        "Semantic/keyword retrieval over the codebase. Returns the most relevant "
        "50-line code chunks ranked by relevance to the query.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    ),
    "ast_search": _schema(
        "ast_search",
        "Search the astimate symbol index. Returns matching symbol entries, each with "
        "symbol path (addr), kind, signature, references (refs), precise line range (loc), "
        "a one-line purpose, and tags. Use this to locate symbols by meaning or structure "
        "without reading full source files.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search by keyword, tag, purpose text, or symbol-path fragment."},
                "top_k": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    ),
    "done": _schema(
        "done",
        "Submit your final answer. Call this once you are confident you have located "
        "the requested symbol(s). Provide every symbol with its file and line range.",
        {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Fully-qualified symbol path, e.g. 'ignore::walk::WalkBuilder::build_parallel'."},
                            "file": {"type": "string", "description": "Repo-relative file path."},
                            "loc_start": {"type": "integer"},
                            "loc_end": {"type": "integer"},
                        },
                        "required": ["symbol", "file", "loc_start", "loc_end"],
                    },
                }
            },
            "required": ["answer"],
        },
    ),
}

CONDITION_TOOLS = {
    "C0": ["read_file", "grep", "done"],
    "C1": ["read_file", "grep", "rag_search", "done"],
    "C2": ["read_file", "grep", "ast_search", "done"],
}


def schemas_for(condition: str) -> list[dict]:
    return [SCHEMAS[n] for n in CONDITION_TOOLS[condition]]


# --------------------------------------------------------------------------- #
# Implementations
# --------------------------------------------------------------------------- #
def _safe_resolve(rel: str) -> Path:
    # Agent paths are relative to the repo root (which contains ripgrep_crates/).
    base = config.REPO_ROOT.resolve()
    cand = (base / rel).resolve()
    if base != cand and base not in cand.parents:
        raise ValueError(f"path escapes repo root: {rel}")
    return cand


def _read_file(args: dict) -> str:
    rel = _arg(args, "path", "file", "filepath", "filename")
    if not rel:
        return "ERROR: read_file requires 'path'"
    try:
        path = _safe_resolve(str(rel))
    except ValueError as e:
        return f"ERROR: {e}"
    if not path.exists() or not path.is_file():
        return f"ERROR: file not found: {rel}"
    start = _arg(args, "start_line", "start", "from", "begin")
    end = _arg(args, "end_line", "end", "to", "stop")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if start is None and end is None:
        s, e = 1, len(lines)
    else:
        s = int(start) if start else 1
        e = int(end) if end else len(lines)
    s = max(1, s)
    e = min(len(lines), e)
    if e - s + 1 > READ_CAP_LINES:
        e = s + READ_CAP_LINES - 1
    out = [f"{i:>5}: {lines[i-1]}" for i in range(s, e + 1)]
    header = f"[{rel}  lines {s}-{e} of {len(lines)}]"
    return header + "\n" + "\n".join(out)


def _grep(args: dict) -> str:
    pattern = _arg(args, "pattern", "regex", "regexp", "query", "search", "term")
    if not pattern:
        return "ERROR: grep requires 'pattern'"
    glob = _arg(args, "glob", "glob_pattern", "filter", default="*.rs")
    try:
        proc = subprocess.run(
            ["rg", "-n", "--no-heading", "-g", glob, pattern, "ripgrep_crates"],
            capture_output=True, text=True, timeout=30, cwd=str(config.REPO_ROOT),
        )
        matches = proc.stdout.splitlines()
    except FileNotFoundError:
        matches = _grep_fallback(pattern, glob)
    if len(matches) > GREP_CAP_MATCHES:
        matches = matches[:GREP_CAP_MATCHES] + [f"... ({len(matches)-GREP_CAP_MATCHES} more matches truncated)"]
    return "\n".join(matches) if matches else "(no matches)"


def _grep_fallback(pattern: str, glob: str) -> list[str]:
    rx = re.compile(pattern)
    out = []
    for p in config.FIXTURE_ROOT.rglob("*"):
        if not p.is_file() or not p.name.endswith(".rs"):
            continue
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    rel = p.relative_to(config.REPO_ROOT)
                    out.append(f"{rel}:{i}:{line}")
        except Exception:
            continue
    return out


# ---- BM25 over precomputed chunk index (lazy-loaded) ----------------------- #
_RAG: dict | None = None


def _load_rag() -> dict:
    global _RAG
    if _RAG is None:
        _RAG = json.loads(config.RAG_INDEX.read_text(encoding="utf-8"))
    return _RAG


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _bm25(query: str, top_k: int) -> list[dict]:
    idx = _load_rag()
    docs = idx["chunks"]
    q_terms = _tokenize(query)
    if not q_terms:
        return []
    N = len(docs)
    avgdl = idx["avgdl"] or 1.0
    k1, b = 1.5, 0.75
    scored = []
    for d in docs:
        tf = d["tf"]
        score = 0.0
        for term in q_terms:
            if term not in tf:
                continue
            df = idx["df"].get(term, 0)
            if df == 0:
                continue
            idf = (N - df + 0.5) / (df + 0.5)
            f = tf[term]
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * (d["len"] / avgdl)))
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:top_k]]


def _rag_search(args: dict) -> str:
    query = _arg(args, "query", "q", "term", "text", "search", "pattern")
    if not query:
        return "ERROR: rag_search requires 'query'"
    top_k = int(_arg(args, "top_k", "k", "n", default=5) or 5)
    hits = _bm25(str(query), top_k)
    if not hits:
        return "(no chunks matched)"
    blocks = []
    for h in hits:
        text = h["text"]
        tlines = text.splitlines()
        if len(tlines) > 40:
            text = "\n".join(tlines[:40]) + f"\n... ({len(tlines)-40} more lines)"
        blocks.append(
            f"--- {h['file']}  lines {h['start']}-{h['end']} ---\n{text}"
        )
    return "\n\n".join(blocks)


# ---- astimate symbol index (lazy-loaded) ---------------------------------- #
_AST: list[dict] | None = None


def _load_ast() -> list[dict]:
    global _AST
    if _AST is None:
        entries = []
        for p in config.ASTIMATE_ROOT.rglob("*.ast"):
            entries.extend(_parse_ast_file(p))
        _AST = entries
    return _AST


def _parse_ast_file(path: Path) -> list[dict]:
    """Parse a .ast sidecar into entry dicts."""
    entries: list[dict] = []
    cur: dict | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            if cur:
                entries.append(cur)
                cur = None
            continue
        if line.startswith("@ "):
            if cur:
                entries.append(cur)
            cur = {"addr": line[2:].strip(), "file": str(path.relative_to(config.ASTIMATE_ROOT)).replace(".ast", "")}
            continue
        if cur is None:
            continue
        if line.startswith("kind "):
            cur["kind"] = line[5:].strip()
        elif line.startswith("sig "):
            cur["sig"] = line[4:].strip()
        elif line.startswith("refs "):
            cur["refs"] = [r.strip() for r in line[5:].split(",") if r.strip()]
        elif line.startswith("loc "):
            cur["loc"] = line[4:].strip()
        elif line.startswith("# "):
            cur["purpose"] = line[2:].strip()
        elif line.startswith("$ "):
            cur["tags"] = [t.strip() for t in line[2:].split(",") if t.strip()]
    if cur:
        entries.append(cur)
    return entries


def _ast_score(entry: dict, q_terms: list[str]) -> float:
    hay = " ".join([
        entry.get("addr", ""),
        entry.get("kind", ""),
        entry.get("sig", ""),
        entry.get("purpose", ""),
        " ".join(entry.get("tags", [])),
    ]).lower()
    tokens = _tokenize(hay)
    if not tokens:
        return 0.0
    score = 0.0
    for q in q_terms:
        score += tokens.count(q)
    return score


def _ast_search(args: dict) -> str:
    query = _arg(args, "query", "q", "term", "text", "keyword", "search", "name", "symbol", "pattern")
    if not query:
        return "ERROR: ast_search requires 'query'"
    top_k = int(_arg(args, "top_k", "k", "n", default=6) or 6)
    entries = _load_ast()
    q_terms = _tokenize(str(query))
    scored = [(_ast_score(e, q_terms), e) for e in entries]
    scored = [(s, e) for s, e in scored if s > 0]
    scored.sort(key=lambda x: -x[0])
    hits = [e for _, e in scored[:top_k]]
    if not hits:
        return "(no symbols matched)"
    blocks = []
    for e in hits:
        lines = [f"@ {e['addr']}"]
        lines.append(f"file {e.get('file', '')}")
        if "kind" in e: lines.append(f"kind {e['kind']}")
        if "sig" in e: lines.append(f"sig {e['sig']}")
        if e.get("refs"): lines.append(f"refs {', '.join(e['refs'][:5])}")
        if "loc" in e: lines.append(f"loc {e['loc']}")
        if "purpose" in e: lines.append(f"# {e['purpose']}")
        if e.get("tags"): lines.append(f"$ {', '.join(e['tags'])}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
class DoneSignal(Exception):
    def __init__(self, answer: list[dict]):
        self.answer = answer


def dispatch(name: str, args: dict) -> str:
    try:
        if name == "read_file":
            return _read_file(args)
        if name == "grep":
            return _grep(args)
        if name == "rag_search":
            return _rag_search(args)
        if name == "ast_search":
            return _ast_search(args)
        if name == "done":
            ans = args.get("answer", [])
            # Accept only a real submission: non-empty list (with >=1 dict) or non-empty prose.
            if isinstance(ans, list) and any(isinstance(x, dict) for x in ans):
                raise DoneSignal([x for x in ans if isinstance(x, dict)])
            if isinstance(ans, str) and ans.strip():
                raise DoneSignal(ans)
            return ("ERROR: your `done` answer was empty. Call `done` AGAIN with a non-empty "
                    "`answer` array. For each symbol give: symbol (fully-qualified path), "
                    "file (repo-relative path), loc_start, loc_end — copied from the tool results.")
        return f"ERROR: unknown tool {name}"
    except DoneSignal:
        raise
    except Exception as e:  # noqa: BLE001  — never kill the run on one bad call
        return f"ERROR in {name}: {type(e).__name__}: {e}"


def parse_args(raw: str) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
