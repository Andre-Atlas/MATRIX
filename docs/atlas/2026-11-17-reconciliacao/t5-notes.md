# T5 — Sweeps sobre `runs` + agente no SDK (notas de trabalho)

Branch `ccr-9443eae6-fk5867`. Base: contrato G2 (`docs/atlas/2026-11-10-plataforma-completa/contracts/g2.md`), ADR-013, T4 (`t4-notes.md`).

## Decisões

- **Tabelas (`sweeps` + `sweep_runs`).** Em vez de reutilizar `mlops_sweeps` diretamente (cujo modelo `MLOpsSweep` ainda reside em `storage/models.py`, intocado por fronteira de tarefas em paralelo), criamos `server/src/matrix_server/sweeps/models.py` com:
  - `sweeps`: esquema canônico G2 (`config` JSON validado, `status` em `running|paused|finished|stopped`, `metric_key`, `metric_goal`, `seed`, `best_run_id` sem FK circular, `best_value`, `next_index` para alocação atômica, `importance_cache`, `created_by`, `created_at`, `stopped_at`).
  - `sweep_runs`: vínculo por trial (`sweep_id`, `run_id`, `trial_index`, `bracket`, `agent_id`, `pruned`, `last_step`, `last_value`, `best_value`) com `UniqueConstraint("sweep_id", "trial_index")` e `UniqueConstraint("sweep_id", "run_id")`.
  - Na exclusão (`DELETE /api/v1/sweeps/{id}`, papel `ADMIN`), `runs.sweep_id` é zerado para preservar os runs e `sweep_runs` é removido em cascata.
- **Concorrência em `POST /sweeps/{id}/next`.** Combina `SELECT ... FOR UPDATE` no PostgreSQL com compare-and-swap otimista em `sweeps.next_index` (`UPDATE sweeps SET next_index = :next WHERE id = :id AND next_index = :cur`) e `UniqueConstraint("sweep_id", "trial_index")` em `sweep_runs`, garantindo configurações distintas sob concorrência tanto em PostgreSQL quanto em SQLite.
- **`bayes` → `tpe`.** `bayes` é aceito como alias de `tpe` tanto no motor canônico (`validate_sweep_config` normaliza `bayes` para `tpe`) quanto no adaptador deprecated `/api/v1/mlops/sweeps*`. O TPE é univariado (γ = 0,25, 10 amostras iniciais configuráveis via `n_startup_trials`, Parzen gaussiano em espaço linear/log para numéricos e categórica suavizada por Laplace para `choice`, 24 candidatos amostrados da densidade dos bons `l(x)` maximizando `l(x)/g(x)`, reprodutível por `seed` + histórico).
- **Poda ASHA / Hyperband.** Degraus em `min_iter * eta^k` até `max_iter`; Hyperband atribui cada run a uma bracket (`0..s_max`, `r0 = min_iter * eta^b`) em round-robin via `trial_index % num_brackets`; nunca poda antes de `min_iter` (ou `r0` da bracket); mantém top `1/eta` quando há ao menos `eta` resultados no degrau. `median` permanece suportado apenas quando criado pelo adaptador deprecated (`allow_median=True`).
- **Importância por Floresta Aleatória.** Calculada sob demanda com `scikit-learn` (`RandomForestRegressor`, features numéricas com `log` aplicado e categóricas one-hot agregadas por parâmetro, normalizadas para somar 1) somente com ≥ 10 runs concluídos (`status == "finished"`), cacheada em `sweeps.importance_cache` por contagem de runs. Sem `scikit-learn` instalado, retorna `importance: null` acompanhado de `importance_note`.
- **Adaptadores deprecated `/api/v1/mlops/sweeps*`.** Movidos de `api/mlops.py` para `api/mlops_compat.py` (`deprecated=True` no OpenAPI + cabeçalho `Deprecation: true`), respaldados pelo novo `sweeps.service`. `POST /api/v1/mlops/runs/{id}/metrics` consulta `sweeps.service.report_metric(..., record_point=False)` para `should_stop`/`stop_reason`.
- **Migração (fragmento).** `server/migrations/fragments/t5_sweeps.py` cria `sweeps` e `sweep_runs`, copia os registros existentes de `mlops_sweeps` convertendo `bayes→tpe`, `categorical→choice`, `cancelled→stopped`, `completed→finished`, `early_stopping→early_stop` e faz backfill de `sweep_runs` a partir de `runs.sweep_id`. `mlops_sweeps` é mantida até a remoção de `MLOpsSweep` de `storage/models.py` (coordenação/T10).
- **SDK Python.** Novos `matrix_sdk.sweep(config, name)` e `matrix_sdk.agent(sweep_id, function, count=None, agent_id=None)` (também em `MatrixClient`); `start_run()` dentro do agente vincula-se automaticamente ao run criado pelo servidor com `run.config` populado e `run.should_stop()` consultando `/report`; runs podados encerram como `killed` e exceções marcam o run como `failed` sem abortar o agente. `MLOpsClient.create_sweep` e `MLOpsClient.run_sweep_agent` passam a emitir `DeprecationWarning`.
- **Testes ajustados sem exclusão.**
  - `server/tests/test_mlops_compat.py`: atualizado `test_deprecated_routes_are_marked_in_openapi` para refletir `/api/v1/mlops/sweeps*` como deprecated e `/api/v1/sweeps*` como canônico.
  - `server/tests/test_mlops_api.py`: atualizado o spy de `test_bayes_sweep_api_passes_metric_name_with_heartbeat` para espionar `matrix_server.sweeps.engine._suggest_tpe` em vez do legado `mlops.sweeps._suggest_bayes`.
  - `server/tests/test_mlops_sweeps.py`: todos os testes originais preservados e passando integralmente.

## Arquivos criados e alterados

### Criados
- `server/src/matrix_server/sweeps/__init__.py`
- `server/src/matrix_server/sweeps/models.py`
- `server/src/matrix_server/sweeps/engine.py`
- `server/src/matrix_server/sweeps/service.py`
- `server/src/matrix_server/api/sweeps.py`
- `server/migrations/fragments/t5_sweeps.py`
- `server/tests/test_sweeps_api.py`
- `sdk/python/src/matrix_sdk/sweeps.py`
- `sdk/python/tests/test_sweeps_sdk.py`
- `docs/atlas/2026-11-17-reconciliacao/t5-notes.md`

### Alterados
- `server/src/matrix_server/app.py` (montagem do router `sweeps`)
- `server/src/matrix_server/api/mlops.py` (remoção das rotas de sweeps movidas para `mlops_compat.py`)
- `server/src/matrix_server/api/mlops_compat.py` (rotas deprecated `/api/v1/mlops/sweeps*` sobre `sweeps.service` + `should_stop` via `sweeps.service.report_metric`)
- `server/tests/test_mlops_compat.py` (inventário OpenAPI de depreciação atualizado)
- `server/tests/test_mlops_api.py` (spy TPE atualizado)
- `sdk/python/src/matrix_sdk/tracking.py` (`run.config`, `run.should_stop()`, contexto de trial do agente em `start_run()`)
- `sdk/python/src/matrix_sdk/client.py` (`MatrixClient.sweep`, `MatrixClient.agent`)
- `sdk/python/src/matrix_sdk/__init__.py` (exports `sweep`, `agent`)
- `sdk/python/src/matrix_sdk/mlops.py` (`DeprecationWarning` em `create_sweep` e `run_sweep_agent`)
