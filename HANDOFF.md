# HANDOFF

## 이번 세션의 핵심 성과

| 항목 | 결과 |
|---|---|
| H6 (LSP/RAG 틈새 실존) | ✅ 두 모델 모두 SUPPORTED |
| GLM-5.2 (결정적 측정) | C2/C1 토큰 비율 0.38, C2 정확도 0.80 > C1 0.60 |
| Q-A (포맷 가치) | ✅ 입증 — Type C acc=1.00 |
| Q-B (모델 적합성) | 27B 불충분 → GLM-5.2 충분 (done 100%) |

## 후속작업 로드맵 (우선순위)

| 우선순위 | 작업 | 위협/목표 |
|---|---|---|
| P0 | A1: 두 번째 코드베이스로 일반화 검증 | 단일 코드베이스 = 가장 강한 남은 반론 |
| P0 | A2/B1: tree-sitter 구조 추출기 (다국어) | Rust 전용 정규식 한계 + 제품 설계 첫 단계 |
| P1 | B2: 의미층 대량 생성 품질 검증 | 12개 hand-authored 한계 |
| P1 | A3: Type D 정확도 동급 해결 | refs 활용도 |
| P1 | B3: astimate CLI 스켈레톤 | 제품화 |
| P2 | B4: watch 데몬 + best-effort 훅 | 합의된 아키텍처 |

**권장 재개 순서: A1+A2 → B2 → A3/A4/A5 → B3 → B4**

## 새 세션에서 재개하는 법

문서 3개가 전부 복원됩니다:
- `docs/experiment-h6.md` (설계 + 결정 규칙)
- `experiment/harness/README.md` (사용법)
- `docs/session-2026-06-28.md` + `docs/followups.md` (결과 + 로드맵)

데이터는 언제든 재생성 가능 (`gen_rag` → `gen_ast` → `run`).

수고 많으셨습니다. 다음 세션에서 `docs/followups.md`의 A1부터 시작하면 자연스럽게 이어집니다.
