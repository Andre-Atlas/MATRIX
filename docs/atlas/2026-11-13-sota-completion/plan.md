# MATRIX — Plano de Implementação: SOTA Completion (Benchmarks, Inspect AI Sandboxes, Zero-Copy Data Versioning & Multimodal Tables)

Spec: [spec.md](spec.md) · Design Brief: [design-brief.md](design-brief.md) · ADR: [docs/adr/007-benchmarks-sandboxes-e-data-versioning.md](../../adr/007-benchmarks-sandboxes-e-data-versioning.md) · Ledger: [ledger.md](ledger.md)

Classificação: `ATLAS · feature · arquitetura+dados+design+produto · large → pesquisa, descoberta, plano (+ADR), G1, SDD, revisão (+adversarial+segurança), verificação, G2, aprendizado · lanes: design, arquitetura`

## Tabela de Custo (`atlas-dispatch` — Gate 1)

| Tarefa | Tag | Nível | Implementa | Revisa |
|---|---|---|---|---|
| **T01** Modelos SQLAlchemy, Migração `0006_sota_completion.py` & Extensão Multimodal `Span.media`/`DatasetItem.media` | `[arch]` | C5 | `pro` | `pro` (`architect`, `database-reviewer`) |
| **T02** Motor Zero-Copy Data Versioning (`DatasetBranch`, `DatasetCommit`, `diff`, 3-way `merge`, `revert`) + API `/api/v1/datasets/{id}/*` | `[arch]` | C5 | `pro` | `pro` (`architect`, `security-reviewer`) |
| **T03** Motor de Benchmarks Plugável (`local_sim`, `http` anti-SSRF, `vllm`) + Worker Handler & API `/api/v1/benchmarks/*` | `[arch]` | C5 | `pro` | `pro` (`architect`, `fastapi-reviewer`, `security-reviewer`) |
| **T04** Motor Inspect AI Sandboxes (`local_sandbox`, `docker` hardened sem socket) + Pipeline `Dataset → Solver → Scorer` & API `/api/v1/sandboxes/*` | `[arch]` | C5 | `pro` | `pro` (`architect`, `security-reviewer`) |
| **T05** SDK Python (`matrix_sdk.benchmarks`, `matrix_sdk.sandboxes`, `matrix_sdk.datasets` branching + multimodal) | `[core]` | C4 | `pro` | `pro` (`python-reviewer`, `security-reviewer`) |
| **T06** Web Core (`types.ts`, `mlops-tokens.ts`, `sota-mocks.ts`, `mock-api.ts`, `layout.tsx` NAV `/benchmarks` + `/sandboxes`) | `[core]` | C3 | `pro` | `pro` (`typescript-reviewer`) |
| **T07** Web `/benchmarks` UI (4 KPIs, Abas `LM-Eval` & `VLMEvalKit`, Matriz Multi-Select, Radar/Grouped Bar SVG + Data Table, Breakdown Drawer, Launch Modal) | `[design]` | C4 | `pro` | `pro` (`design-director`, `a11y-architect`, `react-reviewer`) |
| **T08** Web `/sandboxes` UI (4 KPIs, Launcher `Dataset → Solver → Scorer`, Runs Table, Inspect View Split com Transcrição, Exit Codes, Stdout/Stderr & File Diff) | `[design]` | C4 | `pro` | `pro` (`design-director`, `a11y-architect`, `react-reviewer`) |
| **T09** Web `/datasets` Branching & Multimodal Inspector + `/traces` Multimodal Preview (Branch selector, Commit timeline, Diff/Merge Drawer, SVG Bounding Boxes, Audio, PDF, Text Diff) | `[design]` | C4 | `pro` | `pro` (`design-director`, `a11y-architect`, `react-reviewer`) |
| **T10** Verificação E2E, Testes de Contrato Cruzado, Capturas de Tela & Auditoria Adversarial | `[core]` | C4 | `pro` | `pro` (`audit-lead`, `security-reviewer`) |

## Ordem de Execução (Fases 4–6)

`T01` → (`T02 ∥ T03 ∥ T04`) → (`T05 ∥ T06`) → (`T07 ∥ T08 ∥ T09`) → `T10` → **Gate 2**

## Lista de Tarefas Detalhada

- [x] **T01** `[arch] · C5` — Adicionar os 7 novos modelos (`DatasetBranch`, `DatasetCommit`, `DatasetCommitItem`, `BenchmarkRun`, `BenchmarkTaskResult`, `SandboxRun`, `SandboxTaskTranscript`) e coluna `DatasetItem.media` em `storage/models.py`, estender `Media` em `domain/spans.py`, criar migração `20261114_0006_sota_completion.py`, validar `alembic upgrade head`, `downgrade -1`, `upgrade head`, `alembic check` e testes em `server/tests/test_sota_storage.py`.
- [x] **T02** `[arch] · C5` — Implementar `server/src/matrix_server/datasets/versioning.py` (hashing determinístico, `create_branch`, `commit_changes`, `diff_refs`, 3-way `merge_branches` com LCA, `revert_branch`) e adicionar endpoints `/api/v1/datasets/{id}/branches|commits|diff|merge|revert` e suporte a `media` em `api/datasets.py` + `server/tests/test_dataset_versioning.py`.
- [x] **T03** `[arch] · C5` — Implementar `server/src/matrix_server/benchmarks/runner.py` (catálogo `lm_eval` & `vlmevalkit`, adapters `local_sim`, `http` anti-SSRF, `vllm`), registrar worker handler `"benchmark.run"` e `"mlops.evaluate"`, criar router `server/src/matrix_server/api/benchmarks.py` e registrar em `app.py` + `server/tests/test_benchmarks_api.py`.
- [x] **T04** `[arch] · C5` — Implementar `server/src/matrix_server/sandboxes/runner.py` (`Dataset → Solver → Scorer`, `local_sandbox` isolado + `docker` hardened sem `docker.sock`), registrar worker handler `"sandbox.run"`, criar router `server/src/matrix_server/api/sandboxes.py` e registrar em `app.py` + `server/tests/test_sandboxes_api.py`.
- [x] **T05** `[core] · C4` — Criar `sdk/python/src/matrix_sdk/benchmarks.py`, `sandboxes.py`, estender `datasets.py` (`media`, `create_branch`, `commit`, `diff`, `merge`, `revert`), expor no `MatrixClient` e `__init__.py` + `sdk/python/tests/test_sota_sdk.py`.
- [x] **T06** `[core] · C3` — Atualizar `apps/web/src/lib/types.ts`, estender `mlops-tokens.ts` (tokens de sandbox step & branch diff), criar `sota-mocks.ts`, ligar rotas `/api/v1/benchmarks/*`, `/api/v1/sandboxes/*` e `/api/v1/datasets/*/branches|commits|diff|merge|revert` em `mock-api.ts` e adicionar `/benchmarks` e `/sandboxes` em `layout.tsx`.
- [x] **T07** `[design] · C4` — Construir rota `/benchmarks` (`apps/web/src/app/benchmarks/page.tsx`, `error.tsx`, `components/benchmarks/*`) conforme `design-brief.md` (sem `loading.tsx`, WCAG 2.2 AA, SVG Radar & Grouped Bar + Data Table toggle).
- [x] **T08** `[design] · C4` — Construir rota `/sandboxes` (`apps/web/src/app/sandboxes/page.tsx`, `error.tsx`, `components/sandboxes/*`) conforme `design-brief.md` (Launcher `Dataset → Solver → Scorer` + Inspect View split).
- [x] **T09** `[design] · C4` — Estender `/datasets/[datasetId]` (Branch selector, Commit timeline, Diff/Merge Drawer, W&B Tables Multimodal Inspector com SVG Bounding Boxes, Audio, PDF e Text Diff) e `/traces/[traceId]` (`SpanPanel` com Bounding Boxes & Lightbox resiliente).
- [x] **T10** `[core] · C4` — Integração final: rodar `make test`, `make lint`, `next build`, `alembic check`, capturar screenshots de `/benchmarks`, `/sandboxes`, `/datasets` (branching + multimodal) e rodar revisão adversarial + `security-reviewer` antes do Gate 2.
