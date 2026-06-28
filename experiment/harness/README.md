# H6 experiment harness

Validates **H6**: does the astimate symbol index beat RAG / baseline in the
**LSP/RAG gap** (structure-aware, dependency-contextual, token-efficient symbol
retrieval for AI coding agents)?

See `docs/experiment-h6.md` for the full design and the **decision rule frozen
before measurement**.

## Layout

```
experiment/
  tasks.toml                 # 10 tasks + frozen ground truth (5 types A/B/C/D/E)
  fixtures/ripgrep_crates/   # frozen target source (ripgrep @ dfe4a81)
  harness/
    config.py                # paths, model, limits, .env loader
    llm.py                   # OpenAI-compatible client (stdlib urllib), thinking OFF
    fixtures.py              # task loader
    tools.py                 # tool schemas + impls (read_file/grep/rag_search/ast_search/done)
    judge.py                 # accuracy scoring vs ground truth
    agent.py                 # agent loop (force-search turn 1, nudge/force done)
    run.py                   # orchestrator -> results/runs.csv + summary
    gen_rag.py               # build BM25 chunk index (C1 data)
    gen_ast.py               # build .astimate sidecars (C2 data) + ground-truth self-check
    meaning.csv              # hand-authored meaning layer (12 answer symbols)
```

## Setup

```bash
cp .env.example .env          # fill ASTIMATE_LLM_API_KEY  (NEVER commit .env)
python -m experiment.harness.gen_rag     # build RAG index  -> data/rag_index.json
python- m experiment.harness.gen_ast     # build AST index  -> .astimate/ (+ self-check)
```

`gen_ast` prints `ground-truth self-check: ... OK` — all 12 answer symbols must
be locatable in the generated index. If it reports MISMATCH, the extractor or
tasks.toml needs fixing before running.

## Running

Full sweep (10 tasks x 3 conditions x 5 repeats + warmups = 180 runs):

```bash
python -m experiment.harness.run --conditions C0 C1 C2 --repeats 5
```

Subset / smoke:

```bash
python -m experiment.harness.run --tasks A1 C1 C3 D2 E1 --conditions C0 C1 C2 --repeats 1
```

Output: `results/runs.csv` (one row per run) + a per-condition and per-type
summary printed to stdout, including the **H6 decision view (Type C+D)**.

## Conditions

Identical across conditions: same fixture, same tasks, same model
(`bunker-flash`, thinking OFF), same prompt skeleton, same token/step caps.
The ONLY difference is the available search tool:

| Cond | read_file + grep | search tool |
|------|------------------|-------------|
| C0   | yes              | — (baseline) |
| C1   | yes              | `rag_search` (BM25 over 50-line chunks) |
| C2   | yes              | `ast_search` (astimate symbol index) |

Turn 1 forces the condition's primary search tool, so the agent consults the
index rather than answering from memory. This isolates **index content**
(chunks vs symbols) from **access mechanics** (same tool interface).

## Metrics

- **1st**: total tokens consumed to reach the verified answer.
- 2nd: accuracy (all expected symbols found, loc overlap >= 0.5).
- 3rd: wall-clock; 4th: tool-call count.

Token = sum of prompt+completion across all turns. Accuracy is binary per task
(all expected symbols matched) with a partial-credit column for diagnosis.

## Known model-reliability noise (relevant to Q-B)

The endpoint model (Qwen 27B dense) is noisy at structured tool output: it
sometimes answers in prose (nudged/forced to `done`), submits empty `done`
(rejected + retried), or loops reading files (bounded by step/token caps).
This is intrinsic model behaviour and is part of what H6's companion question
Q-B measures; it averages out over repeats. The decision rule keys on Type C+D
mean/median tokens at held accuracy.
