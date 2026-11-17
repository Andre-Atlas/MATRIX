# T4 — uma pilha de runs, métricas e artefatos (notas de trabalho)

Branch `ccr-9443eae6-fk5867`. Base: ADR-013, contrato G1, ADR-008. Estas notas servem para retomar sem contexto.

## Decisões

- **Runs.** `mlops_training_runs` some; tudo é `runs`. Colunas novas em `runs` (migração 0008): `run_type` e `base_model` (ambas nullable; o `main` filtrava e exibia as duas). Mapeamento:
  - `hyperparams` → `params` (imutáveis; o adaptador nunca altera params depois de criados).
  - `summary_metrics` → calculado de `run_metrics` (`MetricStore.summaries`, último valor por chave sem `sys/`). `PATCH summary_metrics` grava cada valor numérico como ponto da chave no maior passo já registrado no run (último valor vence); valores não numéricos são ignorados.
  - `total_steps` → derivado: maior passo de qualquer chave sem `sys/` (`MetricStore.last_steps`, método novo nos dois stores). `PATCH total_steps` é aceito e ignorado (nem o SDK nem o web do `main` o enviam).
  - `_last_heartbeat_at` (dentro de summary no `main`) → `runs.heartbeat_at`.
  - `hardware_info` → guardado como está em `runs.env["hardware"]` (metadado estático, inclui textos como `gpu_name`) e cada valor numérico também vira ponto `sys/hw.<chave>` no passo 0 (aparece nos painéis de sistema). Se o run já não aceita pontos (fechado há > 10 min), só o `env` é gravado.
  - `classic_eval` → `run_reports` de nome `classic_eval`: `classification` quando há `confusion_matrix {labels, matrix}` (métricas escalares em `metrics`, `roc_curve` vira `roc[0]` do último rótulo, ≤ 200 pontos), `importance` (`method="model"`) quando há `feature_importance`; o que não couber (chaves desconhecidas, `direction`, `threshold`, ou um bloco que a validação tipada recusar) vira um relatório `custom` com o resto. O adaptador reconstrói o JSON do `main` a partir desses relatórios. Um novo `classic_eval` substitui os relatórios `classic_eval` anteriores.
  - `dataset_id` → `dataset_version_id` (string livre; o adaptador ainda valida que o dataset existe no projeto). `sweep_id` e `parent_run_id` já existiam; o adaptador valida que o sweep existe.
  - `finished_at` → `ended_at`; `created_at` → `started_at` (o `runs` não tem `created_at`).
- **Status.** Canônico do branch: `running/finished/failed/killed`. Entrada do `main`: `completed→finished`, `cancelled|stopped→killed`, `queued→running`. Não adicionei `queued` ao enum: o fluxo de sweeps do `main` cria trials já `running` (`suggest_sweep_trial`) e nada consulta `queued`; um run `queued` sem batimento seria falhado pelo reaper em 5 min (risco aceito: o SDK do `main` manda batimento implícito a cada lote de métricas). Saída do adaptador (forma do `main`): `finished→completed`, `killed→cancelled`. Reabrir um run fechado pelo adaptador (`PATCH status=running`) → 409.
- **Métricas.** `training_metrics` (ClickHouse) e `mlops/metrics_store.py` saem; `/mlops/runs/{id}/metrics` grava no `MetricStore` do branch pelo mesmo caminho de `/runs/{id}/metrics` (`runs.service.ingest_points`: validação por ponto, caps, idempotência, aceitação até 10 min após fechar). `category="system"` → chave com prefixo `sys/` (se ainda não tiver); outras categorias são descartadas (a chave é o `metric_name`). `epoch` → ponto da chave `epoch` no mesmo passo: perde-se a possibilidade de épocas diferentes por métrica no mesmo passo (o último vence). Leitura: M4 do branch; `min`/`max` de cada ponto = o próprio valor (M4 devolve pontos reais, não médias); `epoch` preenchido do valor de `epoch` naquele passo; `category` filtra `sys/` (system) ou o resto. Lotes do `main` com ponto inválido não são mais recusados inteiros: aceitação parcial com `rejected` (campo novo na resposta). `should_stop` preservado até a T5.
- **Artefatos.** `mlops/artifact_store.py` (LocalFS + S3 + tokens HMAC + presign) sai. `MlopsArtifact` → `ModelArtifact`, tabela `model_artifacts` (metadado: nome+versão, kind, status `pending_upload→committed`, run, metadata), com `artifact_id` → `artifacts.id` (`ON DELETE SET NULL`) ou `external_uri` (s3/gs/hf/lakefs, só registro, sem rede). Os blobs vão pelo store do branch (dedupe, quota, hash verificado, locks).
  - `presign-upload` → reserva `artifacts` pendente (mesma função de `POST /artifacts/uploads`); se o blob já existe no projeto, conclui na hora (`deduplicated=true`, `upload_url=null`); senão `upload_url` = URL pré-assinada do S3 ou o caminho RELATIVO `/api/v1/artifacts/{id}/content` (o SDK do `main` manda a autenticação só para URLs relativas).
  - `commit` → `complete_upload` do branch; `download-url` → URL pré-assinada do S3 ou `/api/v1/artifacts/{id}/download` (relativa, autenticada).
  - Rotas `/mlops/artifacts/local/{token}`, HMAC próprio, `artifact_token_secret`, `Settings.env`, `ConfigError` e a exigência em compose/.env.example/READMEs/Makefile/verify_mvp removidos (nada mais os usa).
  - Validação de URI externa: função pequena em `mlops/compat.py` (esquema, sem credenciais, host, sem `..`); a checagem de IP privado do `main` saiu (nenhuma requisição é feita a essas URIs; rede a URL de usuário passa pelo `egress_client`).
  - Sandbox (`sandboxes/runner.py`) gravava arquivos no store do `main` sem quota: agora usa `artifacts.service.store_bytes` (linha em `artifacts`, `owner_kind="sandbox_run"`), com quota; `storage_uri = artifact:<id>`.
- **Rotas depreciadas.** `/api/v1/mlops/runs*` e `/api/v1/mlops/artifacts*` ficam em `api/mlops_compat.py`, `deprecated=True` no OpenAPI e cabeçalho `Deprecation: true`. `GET /mlops/runs/{id}/lineage` continua em `api/mlops.py` sem depreciação (não há equivalente canônico; T6).

- **Migração 0008** (`server/migrations/versions/20261117_0008_mlops_runs_into_runs.py`): só PostgreSQL (como a cadeia). Importa `mlops.compat` (status e `classic_eval`); se esse módulo sair na T9/T10, copie as funções para a migração. `summary_metrics`/`total_steps` do `main` viram o relatório `custom` `mlops_summary` (os pontos estão no ClickHouse, fora do alcance do Alembic). Runs com `queued` migram como `running` com `heartbeat_at` = início antigo: o reaper os falha em ≤ 5 min (aceito: `queued` não tinha consumidor). Linhas de projeto inexistente são descartadas. Blobs do store do `main` NÃO são movidos: `model_artifacts.artifact_id` fica nulo e `metadata.legacy_storage_backend/uri` guarda o local antigo (download-url → 410; reenviar com o SDK cria a versão seguinte). Downgrade: recria as tabelas, copia de volta runs com `run_type` (continuam também em `runs`), `classic_eval` reconstruído, `summary_metrics` do `mlops_summary`; perde `run_type/base_model` de runs do G1 e pontos gravados depois do upgrade; artefato com blob G0 volta como `storage_backend="artifact"` (não servível pelo código 0007).
- **Cópia dos pontos do ClickHouse** (manual, uma vez, depois do deploy; validada com `scratchpad/ch_copy.py` contra o ClickHouse local): a versão = `recorded_at` em ns, então escritas novas sempre vencem e, entre duplicatas do `MergeTree`, vence a mais recente. Os contadores de caps (`runs.point_count/key_counts`) não contam os pontos copiados.

```sql
INSERT INTO matrix.run_metrics (project_id, run_id, key, step, ts, value, version)
SELECT project_id, run_id,
       if(category = 'system' AND NOT startsWith(metric_name, 'sys/'), concat('sys/', metric_name), metric_name),
       toUInt64(step), recorded_at, value, toUInt64(toUnixTimestamp64Nano(recorded_at))
FROM matrix.training_metrics WHERE step >= 0
UNION ALL
SELECT project_id, run_id, 'epoch', toUInt64(step), recorded_at, assumeNotNull(epoch), toUInt64(toUnixTimestamp64Nano(recorded_at))
FROM matrix.training_metrics WHERE step >= 0 AND epoch IS NOT NULL;
-- conferido: DROP TABLE matrix.training_metrics;
```

## Estado

- Feito: tudo acima, com testes. Commits `wip(t4)…`, `test(t4)…`, `chore(t4)…` no branch.
- Prova da 0008 (PostgreSQL novo `matrix_t4`): `upgrade 0007` → seed (`scratchpad/seed_0007.sql`: 4 runs, um órfão, `queued`, `cancelled` com pai, `classic_eval` completo, artefato local + externo, versões de modelo, linhagem) → `upgrade head`: 3 runs (`finished/killed/running`), tags em `run_tags`, relatórios `classification` + `importance` + `custom` + `mlops_summary`, `model_artifacts` (externo com URI; local com `legacy_*`), FK das versões para `runs`/`model_artifacts` (a versão do run órfão ficou com `run_id` nulo), tabelas antigas removidas → `alembic check` sem diferenças → `downgrade 0007` (dados de volta, `classic_eval` reconstruído) → `upgrade head` de novo (idempotente: runs já existentes não são duplicados) → `alembic check` limpo.
- Testes reescritos (comportamento descartado trocado pelo novo, sem apagar): `test_mlops_artifacts.py` (store/tokens/segredo do `main` → validação de URI, reserva G0, dedup, quota, chave de API, blob apagado pelo G0, presign estilo S3 com cabeçalhos assinados, ausência de segredo/aliases), `test_mlops_storage.py` (`TrainingRun`/`MemoryTrainingMetricStore` → `Run`/`ModelArtifact`/`MemoryMetricStore` via `compat`; reenvio substitui em vez de duplicar; M4 guarda as pontas; NaN vira null), `test_mlops_api.py` (upload em `/api/v1/artifacts/{id}/content` 204, outro projeto 404, rotas de token 404/405, URIs externas por referência). Novos: `test_mlops_compat.py` (equivalência M4 entre as duas rotas, memória e ClickHouse; run do adaptador é run G1; status; `classic_eval` tipado; OpenAPI deprecated), `test_project_delete_main_modules.py::test_delete_project_purges_mlops_points_and_blobs` (Postgres + store de artefatos + ClickHouse vazios para o projeto apagado).

## Ainda aberto (T5–T10)

- T5: sweeps sobre `runs` de verdade (hoje o motor lê os runs na forma do `main` via `mlops.views.render_runs`); `should_stop` mora no adaptador de métricas; `best_run_id` sem FK.
- T6: linhagem (`GET /mlops/runs/{id}/lineage` não depreciada, sem equivalente canônico), registry (FKs já em `runs`/`model_artifacts`), datasets (`dataset_version_id` é string livre; apagar dataset não limpa mais o vínculo).
- T7: sandboxes já gravam pelo store G0 (`owner_kind="sandbox_run"`); falta expor os artefatos.
- T9/T10: web e SDK do `main` migrarem para `/api/v1/runs*` e `/api/v1/artifacts*`; então remover `api/mlops_compat.py`, `mlops/views.py` e (copiando as funções para a 0008) `mlops/compat.py`. O SDK do `main` só ganhou o envio de `upload_headers` (necessário no S3: `x-amz-checksum-sha256` assinado).
- `httpx.Client` sem `client_kwargs` restantes: benchmarks runner e SDK `mlops` (S3/externo). O HEAD S3 do `mlops` sumiu com o store.

