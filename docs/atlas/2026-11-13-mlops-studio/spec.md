# MATRIX — Spec: MLOps Studio (Tracking de Treino, Sweeps & Model/LoRA Registry) + Higiene Alembic

```
ATLAS · feature · arquitetura+dados+design+produto · large → pesquisa, descoberta, plano (+ADR), G1, SDD (worktrees), revisão (+adversarial+segurança), verificação, G2, aprendizado · lanes: design, arquitetura
```

Origem: [`docs/blueprint-sota.md`](../../blueprint-sota.md) (Módulos 1 e 2) e [`docs/atlas/2026-11-09-plataforma-mvp/report.md`](../2026-11-09-plataforma-mvp/report.md) (Item 3 do roadmap).
ADR: [`docs/adr/006-mlops-tracking-e-model-registry.md`](../../adr/006-mlops-tracking-e-model-registry.md) · Design Brief: [design-brief.md](design-brief.md).

---

## 1. Problema e Objetivos

O MATRIX já cobre observabilidade de inferência, Prompt CMS, avaliação RAG/G-Eval, fila de anotação com export DPO, Vector/Drift, Safety OWASP e AI Gateway, mas ainda não fecha o ciclo de **treinamento/fine-tuning** e **governança de versões de modelos/LoRAs**. Adicionalmente, a tabela `safety_scans` foi adicionada a [`models.py`](../../../server/src/matrix_server/storage/models.py#L370-L387) sem migração Alembic correspondente, fazendo `uv run alembic check` falhar no CI.

Objetivos desta entrega:
1. **Higiene Alembic (`0004_safety_scans`)**: restaurar `uv run alembic check` verde contra PostgreSQL e SQLite.
2. **Training & Fine-Tuning Studio (`/training`)**: rastrear runs (`sft`, `lora`, `qlora`, `dpo`, `grpo`, `ppo`, `classic_ml`), séries temporais de alta frequência (Loss, PPL, Gradientes, Margem DPO, telemetria GPU), métricas clássicas (ROC-AUC, Matriz de Confusão, SHAP/Feature Importance) e comparação de runs.
3. **Hyperparameter Sweeps**: control plane *Ask-and-Tell* (`grid`, `random`, `bayes`) com poda precoce (`asha`, `median`) e visualização em Coordenadas Paralelas SVG com brushing.
4. **Model & LoRA Registry (`/models`)**: catálogo versionado de modelos base, adaptadores LoRA e modelos clássicos com estágios (`none`, `staging`, `production`, `archived`), aliases (`@champion`, `@challenger`), armazenamento presigned (`MinIO`/`LocalFS`, CAS por `sha256`) e grafo DAG completo de linhagem (`Dataset` / `DPO Export` → `TrainingRun` → `Artifact` → `ModelVersion` → `Experiment` / `SafetyScan`).
5. **SDK Python (`matrix_sdk.mlops`)**: `start_run` (context manager com buffer/flush assíncrono em thread daemon), `log`, `log_artifact`, `create_sweep` / `run_sweep_agent`, `register_model` / `create_model_version` / `set_model_alias` / `download_model` e `MatrixTrainerCallback` (duck-typed HF/TRL).

---

## 2. Arquitetura (Resumo do ADR-006)

- **Relacional (PostgreSQL/SQLite)**: 7 novas tabelas portáveis com `project_id` obrigatório (`mlops_sweeps`, `mlops_training_runs`, `mlops_artifacts`, `mlops_artifact_lineage`, `mlops_registered_models`, `mlops_model_versions`, `mlops_model_aliases`), migradas via `0005_mlops_studio.py`.
- **Séries Temporais (`TrainingMetricStore`)**: `ClickHouseTrainingMetricStore` (`MergeTree` + `CODEC(ZSTD(3))` + downsampling) em produção e `MemoryTrainingMetricStore` para dev/testes.
- **Artefatos (`ArtifactStore`)**: `S3ArtifactStore` (SigV4 presigned `PUT`/`GET` em Python puro para MinIO/S3, CAS por `sha256`) e `LocalFSArtifactStore` (URLs locais assinadas por HMAC) para dev/testes.

---

## 3. Contrato da API (`/api/v1/mlops/*`)

Todos os endpoints aplicam isolamento estrito por `ctx.project_id` e RBAC via `require_role`:
- `viewer`: todos os `GET`
- `member`: `POST`/`PATCH` de runs, métricas, sweeps, artefatos, criação de modelos/versões, avaliação, promoção até `staging`/`@challenger`
- `admin`: promoção para `stage="production"` ou movimentação de aliases reservados (`production`, `champion`), e `DELETE /models/{model_id}`

### Endpoints
- **Runs & Séries Temporais:**
  - `POST /api/v1/mlops/runs` → cria `TrainingRun` (valida `dataset_id`, `sweep_id`, `parent_run_id` pertencentes ao mesmo `project_id`; registra aresta `trained_on` se `dataset_id` informado)
  - `GET /api/v1/mlops/runs?status&sweep_id&run_type&base_model&limit&cursor` → `{items, next_cursor}`
  - `GET /api/v1/mlops/runs/{run_id}` → detalhe da run + artefatos + `classic_eval`
  - `PATCH /api/v1/mlops/runs/{run_id}` → atualiza `status`, `total_steps`, `hardware_info`, `classic_eval`, `error`, `finished_at`
  - `POST /api/v1/mlops/runs/{run_id}/metrics` → `{points: [{category, metric_name, step, epoch?, value, recorded_at?}]}` (máx 1000 pontos, `allow_inf_nan=False`) → `{accepted, should_stop, stop_reason}`
  - `GET /api/v1/mlops/runs/{run_id}/metrics?metric_names&category&min_step&max_step&max_points` → séries downsampled
  - `GET /api/v1/mlops/runs/{run_id}/lineage` → `{nodes, edges}` (subgrafo DAG)
- **Sweeps:**
  - `POST /api/v1/mlops/sweeps` → cria sweep (`grid` | `random` | `bayes`, `metric_name`, `metric_goal`, `search_space`, `early_stopping`)
  - `GET /api/v1/mlops/sweeps` → lista sweeps do projeto
  - `GET /api/v1/mlops/sweeps/{sweep_id}` → detalhe + trials + dados de *Parallel Coordinates* + *Hyperparameter Importance*
  - `POST /api/v1/mlops/sweeps/{sweep_id}/trials/next` → sugere hiperparâmetros e cria `TrainingRun` vinculada
  - `POST /api/v1/mlops/sweeps/{sweep_id}/stop` → encerra sweep e sinaliza parada às runs ativas
- **Artefatos (Presigned + CAS):**
  - `POST /api/v1/mlops/artifacts/presign-upload` → cria `Artifact` versionado; deduplica por `(project_id, sha256)` quando já `committed`, ou gera `upload_url` presigned
  - `PUT /api/v1/mlops/artifacts/local/{token}` / `GET /api/v1/mlops/artifacts/local/{token}` → backend `LocalFSArtifactStore` autenticado por assinatura HMAC com expiração (desenvolvimento/testes)
  - `POST /api/v1/mlops/artifacts/{artifact_id}/commit` → marca `committed`, valida existência/tamanho no store e grava arestas de linhagem (`produced`)
  - `GET /api/v1/mlops/artifacts` & `GET /api/v1/mlops/artifacts/{artifact_id}/download-url`
- **Model & LoRA Registry:**
  - `POST /api/v1/mlops/models` / `GET /api/v1/mlops/models` / `GET /api/v1/mlops/models/{model_id}` / `DELETE /api/v1/mlops/models/{model_id}`
  - `POST /api/v1/mlops/models/{model_id}/versions` → registra `ModelVersion` imutável ligada a `artifact_id` + `run_id` + `lora_config` + `signature` + `eval_experiment_id` + `safety_scan_id` + arestas de linhagem
  - `PATCH /api/v1/mlops/models/{model_id}/versions/{version}/stage` → muda estágio (`none`/`staging`/`production`/`archived`; `production` exige `admin`)
  - `POST /api/v1/mlops/models/{model_id}/aliases` → move alias (`production`/`champion` exige `admin`)
  - `GET /api/v1/mlops/models/{model_id}/lineage` → grafo DAG completo da família de versões do modelo
  - `POST /api/v1/mlops/models/{model_id}/versions/{version}/evaluate` → cria e enfileira avaliação vinculada à versão

---

## 4. Segurança e Validações (Gatilhos do ATLAS)

- **Anti-NaN / BodyLimit**: `allow_inf_nan=False` e `json.dumps(..., allow_nan=False)` em métricas, hiperparâmetros e `classic_eval`; limite de 1000 pontos por POST; `BodyLimitMiddleware` em todo `/api/v1/*`.
- **Anti-IDOR**: validação explícita de `project_id` para todas as chaves referenciadas (`dataset_id`, `sweep_id`, `parent_run_id`, `artifact_id`, `run_id`, `eval_experiment_id`, `safety_scan_id`), retornando `404` se pertencerem a outro tenant.
- **Anti-Path-Traversal & Anti-SSRF**: `LocalFSArtifactStore` normaliza e confina caminhos ao diretório raiz configurado; `S3ArtifactStore` aceita `MATRIX_S3_ENDPOINT` apenas via `Settings`; `external` URIs aceitam apenas esquemas `s3://`, `gs://`, `hf://`, `lakefs://` e rejeitam URLs HTTP com IPs privados/loopback/link-local.
- **RBAC**: promoção a `Production` e `@champion` restrita a `admin` (`403 Forbidden` para `member`/`viewer`).

---

## 5. Critérios de Pronto

1. `uv run alembic upgrade head` e `uv run alembic check` passam limpos (inclusive para `safety_scans` e tabelas `mlops_*`).
2. `POST /runs` → `POST /runs/{id}/metrics` (batch) → `presign-upload` → upload binário + `commit` → `POST /models/{id}/versions` → `POST /models/{id}/aliases` funciona fim a fim em `sqlite` + `memory/local` e é coberto por testes de integração + testes de isolamento multi-tenant + RBAC.
3. Sweep Ask-and-Tell (`grid`, `random`, `bayes`) + `asha` early stopping sinaliza `should_stop=true` quando um trial tem desempenho inferior ao corte.
4. `matrix_sdk.mlops` executa uma run simulada de treino/LoRA (incluindo callback e deduplicação de artefato por SHA-256) e passa na suíte `sdk/python`.
5. Rotas web `/training` e `/models` compilam sem erros (`tsc --noEmit`, `next build`), funcionam em `MATRIX_MOCK=1` e contra a API real, respeitam o Design Brief (todos os 5 estados, WCAG 2.2 AA, Coordenadas Paralelas com brushing e Lineage DAG com `@xyflow/react`), com capturas de tela validadas no Gate 2.
