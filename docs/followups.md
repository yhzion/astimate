# Follow-up work candidates

H6 is supported (both bunker-flash and GLM-5.2 sweeps, frozen decision rule).
This file lists the concrete next tasks, grouped by goal, with priority and
the threat each one closes. Pick from here when resuming.

Priority legend: P0 = blocks confidence in the result; P1 = needed before
building the real tool; P2 = nice-to-have / scale.

---

## A. Strengthen the H6 result (close remaining threats)

### A1. Generalize to a second codebase  [P0]
- **Threat:** H6 only proven on ripgrep. "Single codebase" is the strongest
  remaining attack on the conclusion.
- **Do:** add 1–2 fixtures of different shape:
  - a Python project (exercises non-Rust structure extraction),
  - a larger Rust crate (e.g. a tokio subset) to stress scale.
- **Done when:** same decision rule passes on the new fixture(s) with GLM-5.2.

### A2. Multi-language structure extractor  [P0 if A1 includes non-Rust]
- **Threat:** `gen_ast.py` is Rust-only (regex + brace tracking). A Python/TS
  extractor is needed to claim language-agnostic value.
- **Do:** either (a) generalize the regex extractor, or (b) adopt
  `tree-sitter` (the real design choice for the product) and regenerate.
- **Note:** this is also the first real step toward the product (see B1).

### A3. Address the Type D accuracy tie  [P1]
- **Threat:** on dependency-context tasks astimate only wins on efficiency,
  not accuracy — so the "refs" field isn't pulling its weight yet.
- **Do:** inspect D1/D2 failures; check whether the agent ignores `refs`
  output, or whether `refs` is too noisy (currently capped to 5, may need
  relevance filtering). Possibly add a `callers_of(symbol)` tool.

### A4. Raise the baseline floor on Type A  [P1]
- **Threat:** C0 acc 0.10 on Type A inflates C2's relative win.
- **Do:** add 2–3 easier Type A tasks (plain "where is X defined?") so the
  baseline isn't artificially weak; re-run and confirm the verdict holds.

### A5. Statistical significance, not just aggregates  [P2]
- **Threat:** 25 samples/type; verdict currently eyeballed.
- **Do:** bootstrap CIs on trimmed-token ratio and accuracy; record in
  `analyze.py`. Cheap, makes the verdict defensible.

---

## B. Move from harness → real astimate product

### B1. Structure layer via tree-sitter (replace the regex extractor)  [P1]
- The product design uses tree-sitter for deterministic structure; the
  harness currently uses a regex+brace mock to stay stdlib-only.
- **Do:** port `gen_ast.py` to `tree-sitter` (Rust) for at least Rust +
  Python + TypeScript. Keep the harness's mock as a fast fallback.

### B2. Meaning layer generation at scale (Q-B at index-build time)  [P1]
- **Threat:** H6 used a *hand-authored* meaning layer for 12 symbols.
  Real value depends on generating good purpose/tags for thousands.
- **Do:** run `gen_ast.py --gen-meaning` (already implemented) on the full
  fixture; measure purpose quality (a small human eval on ~30 symbols).
  Decide model/temperature/prompt that reaches acceptable quality.

### B3. astimate CLI skeleton  [P1]
- **Do:** a Rust binary `astimate` with: `init`, `index` (build sidecars),
  `query`, hook installer (`pre-push`). Reuses the format spec frozen here.

### B4. Watch daemon + best-effort hook  [P2]
- The agreed architecture (structure-layer real-time via watch; meaning-layer
  batch/on-demand). Build only after B1–B3.

---

## C. Harness / measurement quality

### C1. Determinism test for the structure layer  [P1]
- **Do:** run `gen_ast.py` N times on the same fixture; assert byte-identical
  sidecars. This is the "structure layer is deterministic" claim, made testable.

### C2. Cost model  [P2]
- **Do:** add $ cost and wall-clock-per-symbol for the meaning-generation pass,
  so B2's scale assumptions are grounded.

### C3. Stronger anti-loop / better `ast_search` ranking  [P2]
- Under bunker-flash, C2 had 13 cap_steps collapses. GLM fixed the *symptom*
  (better model), but a more robust ranker + loop guard is still wise before
  weaker models use the tool.

---

## Suggested order to resume

1. **A1 + A2** (second codebase + tree-sitter) — kills the biggest threat and
   doubles as the first product step (B1).
2. **B2** (meaning generation at scale) — the other unverified half of the
   format's value.
3. **A3 / A4 / A5** — tighten the result while B-track work proceeds.
4. **B3 → B4** — build the real tool once the above land.

## Resuming in a new session

Everything needed to continue is in the repo:
- design + decision rule: `docs/experiment-h6.md`
- harness usage: `experiment/harness/README.md`
- this session's outcome: `docs/session-2026-06-28.md`
- this roadmap: `docs/followups.md`

Regenerate data anytime with:
```bash
python -m experiment.harness.gen_rag
python -m experiment.harness.gen_ast
python -m experiment.harness.run   --conditions C0 C1 C2 --repeats 5   # bunker-flash via .env
python -m experiment.harness.run   --conditions C0 C1 C2 --repeats 5 --out results/runs_glm.csv  # GLM
python -m experiment.harness.analyze --csv results/runs_glm.csv
```
