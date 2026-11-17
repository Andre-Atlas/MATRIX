# Ledger — MATRIX MLOps Studio

## Classificação e Decisões (Pré-Gate 1)

- Linha ATLAS: `ATLAS · feature · arquitetura+dados+design+produto · large → pesquisa, descoberta, plano (+ADR), G1, SDD, revisão (+adversarial+segurança), verificação, G2, aprendizado · lanes: design, arquitetura`
- Nota de harness (Antigravity): conforme `.claude/skills/atlas-maestro/references/antigravity.md`, o nível `C1` usa `flash` e `C2–C5` usam `pro` (no `invoke_subagent`, `Model: "inherit"` mantém o modelo da sessão conforme diretiva do ambiente), ignorando o parâmetro de esforço; a tabela canônica do `atlas-dispatch` fica registrada no plano e neste ledger.
- Escopo validado pelo usuário em 2026-11-13:
  1. Higiene imediata de migração Alembic `0004_safety_scans.py` para restabelecer `uv run alembic check`.
  2. Módulo MLOps Studio (Tracking de Treino/Fine-Tuning, Telemetria GPU, Classic ML Eval, Sweeps + Early Stopping, Artifact CAS/Presigned MinIO+LocalFS, Model & LoRA Registry + Lineage DAG, SDK `matrix_sdk.mlops`, UI `/training` e `/models`).

## Rulings

- Ruling (Pré-G1): URLs pré-assinadas S3/MinIO serão implementadas com AWS SigV4 em Python puro (`hmac` + `hashlib`), e `S3` terá paridade local via `LocalFSArtifactStore` com tokens HMAC — evita acoplar `boto3` no core do `server/` e no `sdk/` mantendo modo zero-infra para testes e `make dev-api` — custo se errado: substituir o signatário por `boto3` futuramente só no `artifact_store.py`.
- Ruling (Pré-G1): Rotas web dedicadas `/training` e `/models` em vez de uma única página `/mlops` com muitas sub-abas — segue o Design Brief para separar claramente acompanhamento de runs/sweeps da governança de versões/linhagem e facilitar deep-linking (`?run=`, `?model=`) — custo: 2 links na sidebar em `layout.tsx`.
- Ruling (Pré-G1): Gráficos de séries temporais, Coordenadas Paralelas, ROC-AUC e SHAP construídos em SVG puro acessível (sem adicionar Recharts/ECharts ao `package.json`), mantendo `@xyflow/react` já instalado para o Lineage DAG — mantém bundle enxuto e consistente com `VectorProjector` e `SafetyGauge` — custo: mais código de plotagem em `apps/web/src/components/`.

## Despachos e Execução

- **T01** `[core] · C2` (Implementer: `4339af139ef81bebcb8de8523f383984`): Criada migração `server/migrations/versions/20261113_0004_safety_scans.py`. Verificado com `alembic upgrade head`, `downgrade -1`, `upgrade head`, `alembic check` e `pytest tests/test_safety_scanner.py` (14 passed).
- **T02** `[arch] · C5` (Implementer: `ee9609169045ec1e944fc6b7f6c43574`): Adicionados os 7 modelos SQLAlchemy em `models.py`, migração `20261113_0005_mlops_studio.py`, `TrainingMetricStore` (Memory + ClickHouse downsampling) em `metrics_store.py` e testes em `test_mlops_storage.py` (9 passed, 2 skipped).
- **T03** `[arch] · C4` (Implementer: `334b9442e28d7f750e2bcb0aa36e2b31`): Implementado `ArtifactStore` (SigV4 pure-Python + LocalFS HMAC) em `artifact_store.py`, atualizados `config.py` e `compose.yaml`, e testes em `test_mlops_artifacts.py` (33 passed).
- **T04** `[core] · C5` (Implementer: `1fc8459d8f157975df57d8c8e63bebad`): Implementado motor de sweeps (`grid`, `random`, `bayes` TPE + `asha`/`median`) em `sweeps.py` e testes em `test_mlops_sweeps.py` (20 passed).
- **T05** `[arch] · C5` (Implementer: `18da965ebb4107493af3172244527b2f`): Implementado router `/api/v1/mlops/*` em `api/mlops.py` e testes em `test_mlops_api.py` (3 passed).
- **Revisão Backend (T02–T05)** (`1e654780559fff1ded6eaa471ba55128`): 2 achados HIGH (`bayes` metric_name em `suggest_next_params`, streaming de upload de artefato local contornando o limite de JSON de 2MB do `BodyLimitMiddleware`), 2 MEDIUM (RBAC case-insensitive em aliases e CAS idempotente sem sobrescrita) e 2 LOW (proteção de demote em `production` e `math.isfinite`).
- **Fixer Backend** (`e7b264c7347dc2da181dfd966130491d`): Corrigidos todos os 6 achados com TDD em `sweeps.py`, `artifact_store.py`, `limits.py`, `api/mlops.py` e testes (`92 passed, 14 skipped`, `ruff` limpo).
- **T06** `[core] · C4` (Implementer: `ba85919b156e37a436c10b0a38037c93`): Implementado SDK Python `matrix_sdk.mlops` (`TrainingRun` assíncrono, streaming SHA-256 presigned PUT, sweeps agent, registry CRUD e HF Trainer callback).
- **Revisão SDK (T06)** (`73dfce4fb8d227cc4204889cf19a45f9`): 2 achados HIGH (proteção contra URL protocol-relative `//host/...` vazando `Authorization` e hook `atexit` com `weakref` + `close()`) e 5 MEDIUM (ordenação `stop_reason`/`should_stop`, fila limitada, `NamedTemporaryFile` no download, timeout de streaming e escopo de lock no `finish()`).
- **Fixer SDK** (`0cfa21c66e04a90481ca76755fc94280`): Corrigidos todos os achados em `mlops.py`, `client.py` e testes (`30 passed`, `ruff` limpo).
- **T07** `[core] · C3` (Implementer: `3ef8512b07eadef5650d2bee6227521a`): Criados `mlops-tokens.ts`, `mlops.ts`, `mock-mlops.ts`, atualizados `types.ts`, `mock-api.ts` e `layout.tsx` com rotas `/training` e `/models`.
- **T08** `[design] · C4` (Implementer: `496909a00d471134d0ac818873432ae6`): Criadas rotas e componentes `/training` (Runs & Live Curves SVG com EMA/Log-Y/Data-Table, GPU Telemetry, Sweeps Coordenadas Paralelas com brushing + inputs acessíveis, Classic ML ROC-AUC/Confusion Matrix/SHAP, Compare Drawer e Register Model Modal).
- **T09** `[design] · C4` (Implementer: `12823804efd62f96df75e6bc76d17b01`): Criadas rotas e componentes `/models` (Catálogo com filtros, Model detail com abas Versions & Promotion, Lineage DAG com `@xyflow/react` + toggle Tree List acessível, Model Card & Artifacts com SHA-256 copy, Promote Modal com alerta `@champion` e Version Diff Drawer).
- **T10** `[core] · C4`: Executado `make lint` (server, sdk, web), `alembic check` (No new upgrade operations detected), `pytest server` (475 passed, 133 skipped), `pytest sdk` (30 passed), `vitest apps/web` (182 passed em 15 arquivos) e `npm run build` em `apps/web` (0 erros).

## Rulings Adicionais (Pós-Revisão)

- Ruling: O `BodyLimitMiddleware` aceita `max_artifact_bytes` e isenta rotas de upload streaming de binários (`/api/v1/mlops/artifacts/local/*`) do limite de 2MB aplicado a payloads JSON, usando `request.stream()` e hashing incremental sem alocação em RAM.
- Ruling: URLs pré-assinadas relativas no SDK exigem `url.startswith("/") and not url.startswith("//")` para prevenir bypass por URLs protocol-relative que fariam o `httpx` vazar o header `Authorization`.
- Ruling: No registry de modelos, rebaixar uma versão em `production` requer role `admin`, assim como promover para `production` ou atribuir `@champion`.

