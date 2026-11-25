# T11 — CI, Compose e E2E para as áreas novas

## Resumo das alterações

- **CI (`.github/workflows/ci.yml`)**:
  - `server`: puxa `python:3.12-slim` para habilitar os testes de sandbox Docker em CI, mantém `ruff check`, Postgres (`pgvector/pgvector:pg16`), ClickHouse (`clickhouse/clickhouse-server:24.8`), `scripts/check_skips.py` (incluindo o skip permitido `tests/test_sandboxes_api.py` → `"Docker daemon not available"`) e verificação Alembic (`upgrade head` + `alembic check`).
  - `server-s3`: job opcional (`workflow_dispatch` ou PR com label `ci-s3`) rodando `tests/test_artifacts.py` contra `rustfs/rustfs:latest`.
  - `e2e`: depende de `[server, sdk, web]`, aplica migrações em Postgres real, executa `scripts/verify_mvp.py` (7 critérios com ClickHouse e Postgres reais), instala Chromium do Playwright, faz `npm run build` em `apps/web` e executa `scripts/run_e2e_reconciliation.py` (`--trace off`).
  - `deploy`: valida `docker compose -f deploy/compose.yaml config --quiet` (padrão e `--profile dev`), executa `scripts/check_compose.py` e constrói as imagens `server` e `web`.
- **Compose e ambiente (`deploy/compose.yaml`, `deploy/.env.example`, `scripts/check_compose.py`)**:
  - Adicionadas variáveis `MATRIX_SECRET_KEY`, `MATRIX_SANDBOX_IMAGES` (`python:3.12-slim`), `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ANTHROPIC_API_KEY` e `ANTHROPIC_BASE_URL` em `api`/`worker` sem segredos literais.
  - Serviço opcional `rustfs` restrito a `profiles: ["dev"]` com portas em `127.0.0.1` e credenciais via ambiente (`RUSTFS_ACCESS_KEY`, `RUSTFS_SECRET_KEY`).
  - `scripts/check_compose.py` estendido para validar a presença das variáveis novas, ausência de valores literais em segredos e restrição do `rustfs` ao perfil `dev`.
- **Smoke e E2E (`scripts/verify_mvp.py`, `scripts/run_e2e_reconciliation.py`, `apps/web/e2e/reconciliation.spec.ts`, `apps/web/playwright.config.ts`, `apps/web/package.json`)**:
  - `verify_mvp.py` ganhou o **critério 7**: criação de run canônica, sweep com 2 trials + seleção de `best_run_id`, upload de `BlobArtifact` G0 vinculado a versão de modelo no registry, promoção para `production` + alias `champion` + histórico, consulta de lineage (`run`, `artifact`, `model_version`) e benchmark `local_sim` com `simulated=true`.
  - `run_e2e_reconciliation.py` sobe `matrix-api` e `next start`, executa run + sweep agent + `log_model` + benchmark `local_sim` via `MatrixClient`, e roda Playwright (`reconciliation.spec.ts`, `--trace off`): login via UI → `/training` → `/sweeps/[sweepId]` (Parallel Coordinates) → `/models` (promoção para `production`) → `/lineage` → `/benchmarks` (selo `Simulated`).
- **Ajustes de contrato e revisão T11**:
  - `apps/web/src/app/(app)/benchmarks/page.tsx`, `benchmarks-client.tsx` e `apps/web/src/lib/benchmarks.ts`: desembrulhamento de `{ runs: [...] }` (com fallback `{ items }`), leitura de `summary_metrics.tasks` (com fallback plano sem contar chaves de metadados `total_tasks`/`total_samples`/`mean_primary_score`/`avg_score`) e reconciliação de `selectedRunIds` no `Retry`; coberto em `benchmarks-client.test.tsx` e `benchmarks.test.ts`.
  - Revisão de `architect` e `code-reviewer` aplicada antes do commit: `reconciliation.spec.ts` passou a verificar a promoção para `Production` na linha da tabela de versões e a linha do benchmark com badge `Simulated` + score `%`; `run_e2e_reconciliation.py` usa `client.benchmarks.run(...)`, timeout e dump de logs em falhas; `ci.yml` falha explicitamente no timeout de readiness, importa `models` antes de `drop_all` + remove `alembic_version` e roda `check_skips.py` também no job `server-s3`.

## Verificação local

- `python3 scripts/check_compose.py` → `compose OK`
- `cd apps/web && npx tsc --noEmit && npx vitest run && npx next build` → 46 arquivos, 521 testes passed, build 0
- `make demo` (Postgres `matrix-test-pg` + ClickHouse `matrix-test-ch`) → `MVP: TODOS OS CRITÉRIOS PASSARAM` (7/7)
- `uv run --project sdk/python python scripts/run_e2e_reconciliation.py` (`--trace off`) → `1 passed`
