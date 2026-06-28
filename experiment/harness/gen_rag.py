"""Build the RAG baseline index: 50-line non-overlapping chunks over every .rs
file in the fixture, with precomputed BM25 statistics (tf per chunk, df, avgdl).

Output: experiment/harness/data/rag_index.json
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from . import config

CHUNK_SIZE = 50


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def build() -> dict:
    chunks: list[dict] = []
    files = sorted(config.FIXTURE_ROOT.rglob("*.rs"))
    for fp in files:
        rel = str(fp.relative_to(config.ROOT)).replace("\\", "/")
        lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        for start in range(1, len(lines) + 1, CHUNK_SIZE):
            end = min(start + CHUNK_SIZE - 1, len(lines))
            text = "\n".join(lines[start-1:end])
            tokens = _tokenize(text)
            tf: dict[str, int] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            chunks.append({
                "file": rel, "start": start, "end": end,
                "text": text, "tf": tf, "len": len(tokens) or 1,
            })

    df: dict[str, int] = {}
    for c in chunks:
        for term in c["tf"]:
            df[term] = df.get(term, 0) + 1
    total_len = sum(c["len"] for c in chunks)
    avgdl = total_len / len(chunks) if chunks else 0.0

    return {"chunks": chunks, "df": df, "avgdl": avgdl, "n_chunks": len(chunks),
            "chunk_size": CHUNK_SIZE}


def main() -> int:
    idx = build()
    config.RAG_INDEX.write_text(json.dumps(idx), encoding="utf-8")
    print(f"RAG index: {idx['n_chunks']} chunks across "
          f"{len({c['file'] for c in idx['chunks']})} files, "
          f"avgdl={idx['avgdl']:.1f} -> {config.RAG_INDEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
