# MATRIX — Plano de Implementação: MLOps Studio + Higiene Alembic

Spec: [spec.md](spec.md) · Design Brief: [design-brief.md](design-brief.md) · ADR: [docs/adr/006-mlops-tracking-e-model-registry.md](../../adr/006-mlops-tracking-e-model-registry.md) · Ledger: [ledger.md](ledger.md)

Classificação: `ATLAS · feature · arquitetura+dados+design+produto · large → pesquisa, descoberta, plano (+ADR), G1, SDD, revisão (+adversarial+segurança), verificação, G2, aprendizado · lanes: design, arquitetura`

Nota de ambiente (Antigravity — `references/antigravity.md`): o mapeamento `haiku → flash` (`C1`) e `sonnet`/`opus → pro` (`C2–C5`) substitui a escolha de modelo no harness; a tabela abaixo registra o custo canônico do `atlas-dispatch` (piso de revisor `C2`, piso `security-reviewer` `C4`, esforço teto `high`).

## Tabela de Custo (`atlas-dispatch` — Gate 1)

| Tarefa | Tag | Nível | Implementa | Revisa |
|---|---|---|---|---|
| **T01** Migração Alembic `0004_safety_scans.py` | `[core]` | C2 | `sonnet / low` | `sonnet / low` (`code-reviewer`, `database-reviewer`) |
| **T02** Modelos SQLAlchemy, Migração `0005_mlops_studio.py` & `TrainingMetricStore` (Memory + ClickHouse) | `[arch]` | C5 | `opus / high` | `opus / high` (`architect`, `type-design-analyzer`, `database-reviewer`, `security-reviewer`) |
| **T03** `ArtifactStore` (SigV4 MinIO/S3 + LocalFS HMAC + CAS `sha256` + `compose.yaml`/`config.py`) | `[arch]` | C4 | `opus / medium` | `opus / medium` (`architect`, `security-reviewer`) |
| **T04** Motor de Sweeps (`grid`/`random`/`bayes` + Early Stopping `asha`/`median`) & `app.py` wiring | `[core]` | C5 | `opus / high` | `opus / high` (`code-reviewer`, `python-reviewer`, `security-reviewer`) |
| **T05** API REST `/api/v1/mlops/*` (Runs, Métricas, Sweeps, Artifacts, Models, Versions, Aliases, Lineage, RBAC) | `[arch]` | C5 | `opus / high` | `opus / high` (`architect`, `fastapi-reviewer`, `python-reviewer`, `security-reviewer`) |
| **T06** SDK Python (`matrix_sdk.mlops`: `TrainingRun` async buffer, `log_artifact`, Sweeps, Registry, HF Callback) | `[core]` | C4 | `opus / medium` | `opus / medium` (`code-reviewer`, `python-reviewer`, `security-reviewer`) |
| **T07** Web Core: `types.ts`, `mlops-tokens.ts`, `mlops.ts`, `mock-mlops.ts`, `mock-api.ts` & `NAV` (`layout.tsx`) | `[core]` | C3 | `sonnet / medium` | `sonnet / medium` (`code-reviewer`, `typescript-reviewer`) |
| **T08** Web `/training` UI (Runs, Curvas SVG EMA/Log, GPU Telemetry, Sweeps Parallel Coords, Classic ML ROC/Confusion/SHAP) | `[design]` | C4 | `opus / medium` | `opus / medium` (`design-director`, `a11y-architect`, `react-reviewer`) |
| **T09** Web `/models` UI (Registry, Versões, Promoção de Estágios/Aliases, Lineage DAG com `@xyflow/react` + Tree List) | `[design]` | C4 | `opus / medium` | `opus / medium` (`design-director`, `a11y-architect`, `react-reviewer`) |
| **T10** Verificação E2E, Testes Contrato Cruzado, Capturas de Tela & Auditoria Adversarial | `[core]` | C4 | `opus / medium` | `opus / high` (`adversarial-verifier` / `audit-lead`) |

## Ordem de Execução (Fases 4–6)

`T01` → `T02 ∥ T03` → `T04` → `T05` → (`T06 ∥ T07`) → (`T08 ∥ T09`) → `T10` (revisão + verificação adversarial + capturas) → **Gate 2**

*(Nota: T02/T03 e T06/T07 e T08/T09 tocam pacotes distintos ou contratos já fechados na spec, permitindo execução paralela via subagentes dedicados).*

## Lista de Tarefas Detalhada

- [x] **T01** `[core] · C2` — Criar `server/migrations/versions/20261113_0004_safety_scans.py` (`down_revision = "0003"`) exatamente alinhada a `SafetyScan` em `storage/models.py` (incluindo índice `ix_safety_scans_project_id`). Validar `alembic upgrade head`, `downgrade -1`, `upgrade head` e `uv run alembic check`.
- [x] **T02** `[arch] · C5` — Adicionar os 7 modelos (`MLOpsSweep`, `TrainingRun`, `Artifact`, `ArtifactLineage`, `RegisteredModel`, `ModelVersion`, `ModelAlias`) em `storage/models.py`, criar migração `20261113_0005_mlops_studio.py`, implementar `TrainingMetricStore` (`MemoryTrainingMetricStore` + `ClickHouseTrainingMetricStore` com bucket downsampling) em `server/src/matrix_server/mlops/metrics_store.py` e testes parametrizados.
- [x] **T03** `[arch] · C4` — Implementar `ArtifactStore` em `server/src/matrix_server/mlops/artifact_store.py` (`S3ArtifactStore` SigV4 pure-Python para MinIO/S3 + `LocalFSArtifactStore` com tokens HMAC), atualizar `config.py` (`MATRIX_S3_*`, `MATRIX_ARTIFACT_DIR`, `MATRIX_MAX_ARTIFACT_BYTES`) e `deploy/compose.yaml`, validando anti-SSRF e anti-path-traversal com testes.
- [x] **T04** `[core] · C5` — Implementar `server/src/matrix_server/mlops/sweeps.py` (`grid`, `random`, `bayes` TPE/surrogate, e avaliador incremental `asha`/`median` early stopping) + testes unitários cobrindo determinismo, espaços lineares/log/categóricos e disparo de `should_stop`.
- [x] **T05** `[arch] · C5` — Criar router `server/src/matrix_server/api/mlops.py` com todos os endpoints do contrato `/api/v1/mlops/*`, RBAC (`viewer`/`member`/`admin`), proteção anti-NaN/IDOR, endpoint de upload/download local HMAC, lineage DAG query e registro em `app.py`. Suíte completa em `server/tests/test_mlops_api.py`.
- [x] **T06** `[core] · C4` — Criar `sdk/python/src/matrix_sdk/mlops.py` (`TrainingRun`, buffering assíncrono em thread daemon, `log`, `log_artifact` streaming SHA-256 + presigned PUT, `run_sweep_agent`, Registry CRUD + `download_model` com verificação SHA-256, `MatrixTrainerCallback`), expor no `MatrixClient` e `__init__.py`, e cobrir em `sdk/python/tests/test_mlops_sdk.py`.
- [x] **T07** `[core] · C3` — Atualizar `apps/web/src/lib/types.ts`, criar `mlops-tokens.ts`, `mlops.ts` (formatadores, EMA smoothing, adaptadores seguros), `mock-mlops.ts`, ligar rotas em `mock-api.ts` e adicionar `/training` e `/models` em `app/layout.tsx`, com testes Vitest em `apps/web/src/lib/mlops.test.ts`.
- [x] **T08** `[design] · C4` — Construir rota `/training` (`apps/web/src/app/training/page.tsx` + `error.tsx` + `components/training/*`): KPIs, 3 abas WAI-ARIA, tabela multi-select, gráfico SVG Loss/PPL/DPO com EMA/Log-Y/Data-Table toggle, GPU Telemetry, Coordenadas Paralelas com brushing + inputs de teclado, Classic ML (ROC-AUC, Matriz de Confusão `<table>`, SHAP bars), Compare Drawer e Register Model Modal.
- [x] **T09** `[design] · C4` — Construir rota `/models` (`apps/web/src/app/models/page.tsx` + `error.tsx` + `components/models/*`): KPIs, catálogo com filtros, detalhe com versões imutáveis, modal de Promoção/Alias (`@champion`/`@challenger` com alerta de impacto), Lineage DAG com `@xyflow/react` + toggle Tree List acessível, Model Card & SHA-256 copy, e Version Diff Drawer.
- [x] **T10** `[core] · C4` — Integração final: rodar `make test`, `make lint`, `next build`, `alembic check`, iniciar stack real local (`uvicorn` + `next start`) para teste E2E via API real + modo `MATRIX_MOCK=1`, capturar screenshots das telas `/training` e `/models` e rodar revisão adversarial + `security-reviewer` antes do Gate 2.
