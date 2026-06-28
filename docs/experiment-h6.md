# Experiment H6 — Does the astimate symbol index beat RAG/baseline in the LSP/RAG gap?

> **Status:** DRAFT (decision rule frozen before measurement)
> **Fixture:** ripgrep `crates/` @ commit `dfe4a81d2591daca76d25ae4e052c34b26578155`
> **Endpoint:** `bunker-flash` (Qwen 3.6 27B dense alias) via `https://llm-proxy.datamaker.io` — **fixed across all conditions**

## 1. Purpose

Validate H6: the *core differentiation* of astimate.

> The gap that neither LSP nor RAG fills: **structure-aware, dependency-contextual, token-efficient symbol retrieval for AI coding agents.**

This experiment isolates **Q-A (format value)** from **Q-B (LLM quality)** by freezing the meaning layer (purpose/tags) up front (hand-authored or generated once by a strong model and pinned). If H6 fails *with a perfect meaning layer*, the format itself is worthless regardless of which LLM powers it later.

## 2. Hypothesis & falsifier

- **H6 (claim):** On the same coding agent + model, the astimate condition reaches the verified answer using **significantly fewer tokens** than baseline and RAG, while **holding accuracy**.
- **Falsifier:** If C2 (astimate) token cost is **not** lower than C1 (RAG) on the combined Type-C/D tasks (the gap tasks), the differentiation does not exist → the format is abandoned or re-scoped.

## 3. Conditions (independent variable)

| Cond | What the agent receives | Targets |
|---|---|---|
| **C0 — Baseline** | Repo path only; free `read` / `grep`; no index | ground floor |
| **C1 — RAG** | Source chunked at 50 lines + embedding search tool | what RAG already solves |
| **C2 — astimate** | `.astimate/**/*.ast` sidecars (symbol unit, frozen purpose, loc, refs) | the candidate |

Fixed across all conditions: **same fixture, same tasks, same agent model (`bunker-flash`), same prompt skeleton, same token/step caps.**

## 4. Task taxonomy (the deliberate mix)

The 10 tasks are spread across 5 types so the *gap* is provable, not just "is it useful":

| Type | LSP | RAG | astimate (expected) | Count | Role |
|---|---|---|---|---|---|
| **A. Pure structure** (def/ref) | strong | weak | ~even | 2 | **Baseline floor** — astimate should NOT lose here |
| **B. Pure semantic** (keyword) | weak | strong | ~even | 2 | **Baseline floor** — astimate should NOT lose here |
| **C. Structure + semantic** | weak | weak | **win (gap)** | 3 | **H6 core** |
| **D. Dependency context** (multi-symbol) | partial | weak | **win (gap)** | 2 | **H6 core** |
| **E. Precise loc slicing** | strong | weak | **win** | 1 | astimate strength |

**Decision-relevant subset:** Type C+D (5 tasks). If C2 does not win *there*, the gap does not exist.

## 5. Metrics

| Metric | Priority | How |
|---|---|---|
| Tokens consumed to reach the verified answer | **1st** | sum prompt+completion of agent steps until answer |
| Accuracy (hits verified symbol + loc) | 2nd | exact symbol match AND loc overlap ≥ 50% |
| Wall-clock | 3rd | end-to-end per run |
| Tool-call / file-read count | 4th | instrumentation |

## 6. Scale

- 10 tasks × 3 conditions × **5 repeats** = **150 runs**
- Order: randomize condition order per task; randomize task order per repeat; discard first run of each (warm-up).

## 7. Decision rule (frozen before measurement)

Evaluate on the **Type C+D subset (5 tasks)**, accuracy-held (≥ 0.8 in each condition, else that condition is "failed"):

| Outcome | Verdict | Action |
|---|---|---|
| C2 tokens **< 0.7 × C1** (≥30% reduction), accuracy held | **H6 supported** | Proceed to full design / build |
| C2 tokens **within 0.7–1.0 × C1**, accuracy held | **H6 weak** | Re-scope differentiation before building |
| C2 tokens **≥ C1**, or accuracy drops | **H6 falsified** | Abandon the format |

Sanity checks (must also hold, else rerun):
- On Type A: C2 must be within 2× of C0 (astimate must not regress structure tasks).
- On Type B: C2 must be within 2× of C1 (astimate must not regress semantic tasks).

## 8. Pitfall controls (hardcoded)

- **Meaning layer frozen** → isolates Q-A from Q-B.
- **Same model across conditions** → no model bias.
- **Ground truth fixed before runs** → no post-hoc reinterpretation.
- **Token cap + step cap per run** → prevents runaway.
- **Agent cannot read `.astimate/` in C0/C1** → no contamination.

## 9. Artifacts

- `experiment/tasks.toml` — the 10 tasks + verified ground truth.
- `experiment/fixtures/ripgrep_crates/` — frozen target source.
- `.env.example` — endpoint config (copy to `.env`, never commit).

## 10. Next

Write `experiment/harness/` to drive the 3 conditions and collect metrics, then execute.
