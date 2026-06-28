"""Generate the astimate symbol index (.ast sidecars) — structure layer.

Deterministic, tree-sitter-free extraction (regex + brace tracking). Verified
against the frozen ground truth in tasks.toml for the answer symbols.

  * module path derived from file path
  * pub items (struct/fn/trait/enum/type) + impl-block methods extracted
  * loc computed by brace matching
  * refs = best-effort: other indexed symbol names appearing in the body
  * meaning layer (# purpose, $ tags) merged from meaning.csv; symbols missing
    authored meaning get structure-only entries (no # / $). A separate
    meaning-generation pass via the proxy can fill them (--gen-meaning).

Output: experiment/.astimate/ripgrep_crates/.../*.ast  (mirrors the fixture).
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import sys
from pathlib import Path

from . import config, llm

# --------------------------------------------------------------------------- #
# Module-path derivation
# --------------------------------------------------------------------------- #
def _module_of(fp: Path) -> str:
    rel = fp.relative_to(config.REPO_ROOT).as_posix()          # ripgrep_crates/ignore/src/walk.rs
    assert rel.startswith("ripgrep_crates/"), rel
    rest = rel[len("ripgrep_crates/"):]                         # ignore/src/walk.rs
    if "/src/" in rest:
        crate, sub = rest.split("/src/", 1)
    elif rest.endswith("/src.rs") or rest == "src.rs":
        crate, sub = rest[:-len("/src.rs")], ""
        rest = rest[:-3]
        sub = ""
    else:
        crate, sub = rest, ""
    sub = sub[:-3] if sub.endswith(".rs") else sub              # strip .rs
    segs = [s for s in sub.split("/") if s and s not in ("mod", "lib")]
    return "::".join([crate, *segs]) if segs else crate


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
PUB_ITEM = re.compile(
    r"^\s*pub(?:\s*\([^)]*\))?\s+(?:async\s+|const\s+|unsafe\s+)*"
    r"(struct|enum|trait|fn|type|const|static)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
IMPL_HEAD = re.compile(
    r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?impl(?:<[^>]*>)?\s+(.*)$"
)
IMPL_NAME = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")


def _strip_for_braces(line: str) -> str:
    """Remove line comments and double-quoted string bodies so brace counting
    is not corrupted by braces inside strings/comments. Lifetimes/char literals
    are left as-is (rare to contain braces in this codebase)."""
    out = []
    i, n = 0, len(line)
    in_str = False
    while i < n:
        c = line[i]
        nx = line[i + 1] if i + 1 < n else ""
        if in_str:
            if c == "\\":
                out.append("  ")
                i += 2
                continue
            if c == '"':
                in_str = False
                out.append('"')
                i += 1
                continue
            out.append(" ")
            i += 1
            continue
        if c == "/" and nx == "/":
            break
        if c == '"':
            in_str = True
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _depth_profile(lines: list[str]) -> list[int]:
    """depth_after[i] = brace nesting depth after processing line i."""
    d = 0
    out = []
    for line in lines:
        clean = _strip_for_braces(line)
        d += clean.count("{") - clean.count("}")
        out.append(d)
    return out


def _close_line(depth_after: list[int], start_idx: int, return_to: int) -> int:
    """First line j >= start_idx whose depth_after[j] <= return_to."""
    for j in range(start_idx, len(depth_after)):
        if depth_after[j] <= return_to:
            return j
    return len(depth_after) - 1


def _impl_target(head_tail: str) -> str | None:
    if " for " in head_tail:
        left = head_tail.split(" for ")[0]
    else:
        left = head_tail
    m = IMPL_NAME.search(left)
    return m.group(1) if m else None


TRAIT_HEAD = re.compile(r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?trait\s+([A-Za-z_][A-Za-z0-9_]*)")
BARE_FN = re.compile(r"^\s*(?:async\s+|const\s+|unsafe\s+)*fn\s+([A-Za-z_][A-Za-z0-9_]*)")


def _item_loc(lines: list[str], depth_after: list[int], i: int) -> tuple[int, int]:
    """Loc span (1-indexed) for an item starting at line index i.
    Handles multi-line signatures: ends at the terminating ';' (declaration) or
    at the closing brace of the body, whichever comes first."""
    D = depth_after[i - 1] if i > 0 else 0   # depth at start of line i
    for k in range(i, len(lines)):
        clean = _strip_for_braces(lines[k])
        semi = clean.find(";")
        brace = clean.find("{")
        if brace != -1 and (semi == -1 or brace < semi):
            end = _close_line(depth_after, k, D)
            return (i + 1, end + 1)
        if semi != -1:
            return (i + 1, k + 1)
    return (i + 1, len(lines))


def extract_file(fp: Path) -> list[dict]:
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    depth_after = _depth_profile(lines)
    module = _module_of(fp)
    file_rel = fp.relative_to(config.REPO_ROOT).as_posix()
    entries: list[dict] = []

    # Collect container scopes (impls + traits) for method attribution.
    # containers: (start_idx, end_idx, target_name, kind)
    containers: list[tuple[int, int, str | None, str]] = []
    for i, line in enumerate(lines):
        m_impl = IMPL_HEAD.match(line)
        if m_impl and "{" in line:
            target = _impl_target(m_impl.group(1).strip())
            end = _close_line(depth_after, i, depth_after[i - 1] if i > 0 else 0)
            containers.append((i, end, target, "impl"))
            entries.append({
                "addr": f"{module}::{target}" if target else f"{module}::<anon-impl-{i}>",
                "kind": "impl",
                "sig": line.strip().split("{")[0].strip() + " { ... }",
                "loc": (i + 1, end + 1), "file": file_rel, "module": module,
            })
            continue
        m_tr = TRAIT_HEAD.match(line)
        if m_tr and "{" in line:
            target = m_tr.group(1)
            end = _close_line(depth_after, i, depth_after[i - 1] if i > 0 else 0)
            containers.append((i, end, target, "trait"))
            # (the trait itself is recorded below as a pub item)

    def _enclosing_container(k: int) -> tuple[str, str] | None:
        for (s, e, t, kind) in reversed(containers):
            if s < k <= e:
                return (t, kind)
        return None

    # Items.
    for i, line in enumerate(lines):
        kind: str | None = None
        name: str | None = None
        m_item = PUB_ITEM.match(line)
        if m_item:
            kind, name = m_item.group(1), m_item.group(2)
        else:
            cont = _enclosing_container(i)
            if cont and cont[1] in ("impl", "trait"):
                m_fn = BARE_FN.match(line)
                if m_fn:
                    kind, name = "fn", m_fn.group(1)
        if not kind or not name:
            continue

        cont = _enclosing_container(i)
        if cont and cont[0] and cont[1] in ("impl", "trait"):
            addr = f"{module}::{cont[0]}::{name}"
        else:
            addr = f"{module}::{name}"
        s, e = _item_loc(lines, depth_after, i)
        entries.append({
            "addr": addr, "kind": kind,
            "sig": _signature_of(line, lines, i),
            "loc": (s, e), "file": file_rel, "module": module,
        })
    return entries


def _join(lines, a, b):
    return "\n".join(lines[a:max(b, a + 1)])


def _find_brace_line(lines, start):
    for j in range(start, min(start + 20, len(lines))):
        if "{" in lines[j]:
            return j
    return None


def _signature_of(line, lines, idx):
    # capture up to '{' or end of statement across a few lines
    buf = []
    for j in range(idx, min(idx + 6, len(lines))):
        buf.append(lines[j])
        if "{" in lines[j] or ";" in lines[j]:
            break
    text = " ".join(s.strip() for s in buf)
    text = re.sub(r"\s+", " ", text)
    return text.split("{")[0].strip().rstrip(";").strip()


# --------------------------------------------------------------------------- #
# Best-effort refs
# --------------------------------------------------------------------------- #
def _compute_refs(entries: list[dict]) -> None:
    by_short = {}
    for e in entries:
        short = e["addr"].split("::")[-1]
        by_short.setdefault(short, []).append(e["addr"])
    for e in entries:
        if e["kind"] == "impl":
            e["refs"] = []
            continue
        s, en = e["loc"]
        body = "\n".join(_lines_cache[e["file"]][s - 1:en])
        refs = []
        seen = set()
        for short, addrs in by_short.items():
            if short == e["addr"].split("::")[-1]:
                continue
            if re.search(r"\b" + re.escape(short) + r"\b", body):
                for a in addrs:
                    if a != e["addr"] and a not in seen:
                        refs.append(a)
                        seen.add(a)
        e["refs"] = refs[:20]


_lines_cache: dict[str, list[str]] = {}


# --------------------------------------------------------------------------- #
# Meaning layer
# --------------------------------------------------------------------------- #
def _load_authored() -> dict[str, dict]:
    out = {}
    with config.MEANING_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["addr"].strip()] = {
                "purpose": row["purpose"].strip(),
                "tags": [t.strip() for t in row["tags"].split("|") if t.strip()],
                "kind": row.get("kind", "").strip(),
            }
    return out


def _gen_missing_meaning(entries: list[dict], cache_path: Path) -> None:
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    sysinstr = ("You annotate a Rust symbol. Given its signature and doc context, reply with "
                "ONE concise English line describing its purpose, then a line of <=5 lowercase "
                "comma-separated tags. Reply EXACTLY as:\n"
                "PURPOSE: <line>\nTAGS: a, b, c")
    for e in entries:
        if e.get("purpose"):
            continue
        if e["addr"] in cache:
            e["purpose"] = cache[e["addr"]].get("purpose", "")
            e["tags"] = cache[e["addr"]].get("tags", [])
            continue
        # build context: signature + body excerpt
        s, en = e["loc"]
        body = "\n".join(_lines_cache[e["file"]][s - 1:min(en, s + 14)])
        prompt = f"{sysinstr}\n\nsymbol: {e['addr']}\nkind: {e['kind']}\nsignature: {e['sig']}\n\ncontext:\n{body}"
        try:
            tr = llm.chat(
                [{"role": "system", "content": sysinstr},
                 {"role": "user", "content": f"symbol: {e['addr']}\nkind: {e['kind']}\nsignature: {e['sig']}\n\ncontext:\n{body}"}],
                thinking=False,
            )
            txt = (tr.content or "").strip()
            purpose, tags = _parse_meaning(txt)
        except Exception as ex:  # noqa: BLE001
            print(f"  gen-meaning fail {e['addr']}: {ex}", file=sys.stderr)
            purpose, tags = "", []
        e["purpose"], e["tags"] = purpose, tags
        cache[e["addr"]] = {"purpose": purpose, "tags": tags}
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    config.MEANING_GEN_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")


def _parse_meaning(text: str) -> tuple[str, list[str]]:
    purpose, tags = "", []
    for line in text.splitlines():
        ls = line.strip()
        if ls.lower().startswith("purpose:"):
            purpose = ls.split(":", 1)[1].strip()
        elif ls.lower().startswith("tags:"):
            tags = [t.strip() for t in ls.split(":", 1)[1].split(",") if t.strip()]
    if not purpose:
        purpose = text.splitlines()[0].strip()[:140] if text.strip() else ""
    return purpose, tags


# --------------------------------------------------------------------------- #
# Emit .ast
# --------------------------------------------------------------------------- #
def _emit(entries: list[dict]) -> int:
    by_file: dict[str, list[dict]] = {}
    for e in entries:
        by_file.setdefault(e["file"], []).append(e)
    n = 0
    for file_rel, group in by_file.items():
        out_path = config.ASTIMATE_ROOT / (file_rel + ".ast")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        group.sort(key=lambda e: e["loc"][0])
        chunks = []
        for e in group:
            lines = [f"@ {e['addr']}"]
            lines.append(f"kind {e['kind']}")
            if e.get("sig"):
                lines.append(f"sig {e['sig']}")
            if e.get("refs"):
                lines.append(f"refs {', '.join(e['refs'])}")
            lines.append(f"loc {e['loc'][0]}-{e['loc'][1]}")
            if e.get("purpose"):
                lines.append(f"# {e['purpose']}")
            if e.get("tags"):
                lines.append(f"$ {', '.join(e['tags'])}")
            chunks.append("\n".join(lines))
            n += 1
        out_path.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    return n


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen-meaning", action="store_true",
                    help="generate missing meaning via the proxy (cached)")
    args = ap.parse_args(argv)

    files = sorted(config.FIXTURE_ROOT.rglob("*.rs"))
    entries: list[dict] = []
    for fp in files:
        _lines_cache[fp.relative_to(config.REPO_ROOT).as_posix()] = \
            fp.read_text(encoding="utf-8", errors="replace").splitlines()
        entries.extend(extract_file(fp))

    _compute_refs(entries)

    authored = _load_authored()
    for e in entries:
        a = authored.get(e["addr"])
        if a and (not a["kind"] or a["kind"] == e["kind"]):
            e["purpose"] = a["purpose"]
            e["tags"] = a["tags"]

    if args.gen_meaning:
        print(f"generating meaning for {sum(1 for e in entries if not e.get('purpose'))} symbols ...")
        _gen_missing_meaning(entries, config.MEANING_GEN_CACHE)

    n = _emit(entries)
    print(f"astimate index: {n} entries across {len(files)} files -> {config.ASTIMATE_ROOT}")
    _verify_ground_truth(entries)
    return 0


def _verify_ground_truth(entries):
    """Self-check: every expected (addr,loc) in tasks.toml must appear in the index."""
    try:
        from .fixtures import load_tasks
    except Exception:
        return
    by_addr = {}
    for e in entries:
        by_addr.setdefault(e["addr"], []).append(e["loc"])
    problems = 0
    for t in load_tasks():
        for exp in t.expected:
            locs = by_addr.get(exp.symbol, [])
            ok = any(_overlap(l, exp.loc) >= 0.5 for l in locs)
            if not ok:
                problems += 1
                print(f"  GROUND-TRUTH MISS: {t.id} {exp.symbol} exp={exp.loc} got={locs}")
    print(f"ground-truth self-check: {len(by_addr)} addrs; "
          f"{'OK' if problems == 0 else f'{problems} MISMATCH(ES)'}")


def _overlap(a, b):
    s = max(a[0], b[0]); e = min(a[1], b[1])
    inter = max(0, e - s + 1)
    union = (a[1] - a[0] + 1) + (b[1] - b[0] + 1) - inter
    return inter / union if union else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
