# Relatório final — Reconciliação `main` → `feat/reconciliacao-t5-t13` (T1–T13)

Branch `feat/reconciliacao-t5-t13` · `origin/main` (`2b00014`) é ancestral · 19 commits · 203 arquivos, +23193/−5253.

## Evidência de verificação (bancos reais: Postgres 16 e ClickHouse 24.8 em Docker)

| Verificação | Resultado |
|---|---|
| Servidor `ruff check .` | limpo |
| Servidor `pytest -rxXs` (Postgres + ClickHouse) | **1409 passed, 84 skipped, 0 falhas** |
| `scripts/check_skips.py` | 84 permitidos (S3 sem endpoint, perf estrutural), **0 inesperados** |
| Alembic em banco novo | `upgrade head` → `alembic check` limpo → `downgrade base` → `upgrade head`; head `0009` |
| SDK `ruff` + `pytest` | 119 passed; 3 falhas de ambiente (`libomp.dylib` ausente no macOS: xgboost/lightgbm/shap) |
| Web `tsc --noEmit` + `vitest` + `next build` | 48 arquivos, **544 testes**, build ok |
| `scripts/check_compose.py` | `compose OK` |
| `make demo` (`verify_mvp.py`) | **7/7 critérios** |
| Playwright E2E `reconciliacao` (`--trace off`) | **1 passed** (login → run → sweep → registry/promoção → lineage → benchmark simulado com score) |

## Entregas
T1 ADR-013 · T2 merge do `main` + cadeia Alembic · T3 RBAC nos routers do `main` · T4 runs/métricas/artefatos unificados · T5 sweeps · T6 registry/linhagem/datasets · T7 sandboxes + benchmarks · T8 gateway/safety/vetores · T9 web · T10 SDK · T11 CI/compose/e2e · T12 auditoria final · T13 este relatório.

## T12 — auditoria final
4 auditores paralelos + `audit-lead` ([audit-final.md](audit-final.md)): **5 CRITICAL e 17 HIGH, todos confirmados e corrigidos** (commits `8e9932f`…`e8e04bb`), entre eles: exfiltração de variáveis de ambiente via `api_key_env`, circuit breaker global entre tenants, `mlops.evaluate` descartado em silêncio, fallback para mock no gateway, score sintético com `simulated=false`, scorer desconhecido aprovando tarefa, I/O de banco bloqueando o event loop do gateway. **23 MEDIUM e 4 LOW ficam como pendências** no relatório de auditoria.

## Decisões (Rulings no ledger)
`sync_exec` de benchmarks só em teste (E2E/demo usam worker real) · `HttpBenchmarkEngine` rotulado `simulated=true` · `api_key_env` restrito a `MATRIX_BENCH_API_KEY_*` ou allowlist · loggers Alembic não desabilitados.

## Riscos abertos / não validado
- Motores reais (Inspect AI, lm-eval, VLMEvalKit, UMAP/HDBSCAN): só interfaces/stubs; resultados simulados rotulados.
- S3 real (RustFS): job `server-s3` do CI só roda com label `ci-s3`/dispatch; sem validação local (84 skips).
- GPU/NVML, `docker compose up` completo e CI do GitHub não executados nesta sessão.
- 3 testes do SDK falham localmente por falta de `libomp` (ambiente macOS).
- `npm audit` de produção: 3 vulnerabilidades pré-existentes (`next`/`postcss`, `echarts`).
- 23 MEDIUM + 4 LOW da auditoria (ex.: ReDoS em scorer regex, `sweep_id`/`dataset_commit_id` sem checagem de projeto, N+1 em reports de treino).

## Estado git
Árvore limpa (exceto `MATRIX/` e `.DS_Store` não rastreados); nada enviado ao remoto; sem PR.
