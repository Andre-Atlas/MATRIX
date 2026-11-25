# T6 — Registry de modelos/LoRA, linhagem limitada e datasets (notas de trabalho)

Branch `ccr-9443eae6-fk5867`. Base: ADR-012 (egress), ADR-013, ADR-014, ADR-015, contrato G3.

## Decisões tomadas

### 1. Model & LoRA Registry (`mlops/registry_models.py`, `mlops/webhooks.py`, `api/mlops.py`)
- **`production` único por modelo:**
  - Índice único parcial `uq_mlops_model_versions_one_production` em `mlops_model_versions (model_id) WHERE stage = 'production'` (SQLite e PostgreSQL).
  - Ao promover uma versão a `production` (via `PATCH/POST .../versions/{v}/stage` ou criação direta com `stage="production"`), o servidor faz `SELECT ... FOR UPDATE` na linha do `RegisteredModel` (PostgreSQL), rebaixa qualquer versão anterior em `production` para `archived` na mesma transação (`flush` antes de promover a nova) e grava ambas as transições em `model_stage_history(id, project_id, model_id, version, from_stage, to_stage, actor_kind, actor_id, comment, ts)`.
  - Histórico exposto em `GET /api/v1/mlops/models/{id}/history` e `GET /api/v1/models/{id}/history` (`VIEWER`), com filtro opcional por `version` e `limit`.
- **Aliases e papéis:**
  - Validação `^[a-z][a-z0-9_-]{0,49}$` após checagem case-insensitive de aliases protegidos (`champion`, `production`) para garantir que `MEMBER` tentando `CHAMPION`/`Production` continue recebendo `403` (compatibilidade com `test_mlops_api.py`).
  - Adicionadas rotas `PUT /api/v1/mlops/models/{id}/aliases/{alias}`, `DELETE /api/v1/mlops/models/{id}/aliases/{alias}` e equivalentes canônicos `/api/v1/models/{id}/aliases/{alias}`.
  - Promoção a `production`, arquivamento, rebaixamento a partir de `production`, alias `champion`/`production` e gestão de webhooks exigem `ADMIN`.
- **Webhooks de CI/CD assinados via `egress_client`:**
  - Rotas `POST/GET/DELETE /api/v1/mlops/models/{id}/webhooks` (e `/api/v1/models/{id}/webhooks`) exigem `ADMIN`; `GET .../webhooks/{webhook_id}/deliveries` exige `VIEWER` e nunca expõe corpo de resposta nem segredo.
  - Segredo gerado automaticamente (`whsec_...`) quando omitido, exibido **uma única vez** na resposta `201`, cifrado em repouso com Fernet (`cryptography`) derivado via SHA-256 de `MATRIX_SECRET_KEY` (`Settings.secret_key`). Sem `MATRIX_SECRET_KEY`, `POST .../webhooks` recusa com `503`.
  - Entrega assíncrona pelo job `webhooks.deliver` (`workers/webhook_handlers.py`, `max_attempts=5`, backoff exponencial do runner `2**attempts`s) usando **exclusivamente** `egress_client(purpose="webhook")` (ADR-012: allowlist do projeto, IP pinado, revalidação de redirects, bloqueio de IPs privados/link-local/literais).
  - Cabeçalhos enviados: `X-Matrix-Signature: sha256=<hmac(secret, timestamp + "." + body)>`, `X-Matrix-Timestamp`, `X-Matrix-Event`, `X-Matrix-Delivery`.
  - Rejeições de política de saída (ex.: `host_not_allowed`, `ip_literal_not_allowed`, `private`/`link_local`) marcam a entrega como `failed` imediatamente sem retry inútil; erros transitórios (`timeout`, `connection_error`, `dns_failure` ou HTTP >= 400) mantêm `status="queued"` e re-enfileiram até 5 tentativas, persistindo contadores em transação própria antes do rollback do runner.
- **Download de arquivos de versão:**
  - `GET /api/v1/mlops/models/{id}/versions/{v}/files` e `GET /api/v1/models/{id}/versions/{v}/files` (`VIEWER`) retornam os arquivos ligados à versão via store G0 (`get_store(app)`): URL pré-assinada do S3 ou rota autenticada `/api/v1/artifacts/{blob_id}/download`, com cabeçalhos `Cache-Control: private, no-store` e `X-Content-Type-Options: nosniff`.
- **Auditoria:** `audit.record(...)` chamado em transições de estágio (`model.stage_change`), aliases (`model.alias_set`, `model.alias_delete`), criação e remoção de webhooks (`model.webhook_create`, `model.webhook_delete`) — nunca contendo segredos.

### 2. Linhagem limitada (`mlops/lineage.py`, `GET /api/v1/lineage`)
- Substituído o BFS em memória sobre todas as arestas/entidades do projeto por travessia BFS indexada por salto (`query_bounded_lineage`): cada salto consulta apenas as arestas de `mlops_artifact_lineage` e FKs estruturais adjacentes à fronteira atual dentro de `project_id`, e enriquece apenas os nós visitados.
- `GET /api/v1/lineage?node=<kind>:<id>&direction=up|down|both&depth=3` (`VIEWER`): teto rígido de 500 nós com sinalização `truncated: bool`; suporta `data_version`/`dataset_commit`, `dataset`, `run`, `model_version`, `experiment`, `sweep`, `benchmark`, `artifact`, `safety_scan`.
- `GET /api/v1/mlops/runs/{id}/lineage` e `GET /api/v1/mlops/models/{id}/lineage` mantidos sobre a nova consulta limitada (agora retornando também `truncated`).

### 3. Datasets (`datasets/versioning.py`, `api/datasets.py`, `api/runs.py`)
- Papéis verificados em `api/datasets.py`: todas as leituras usam `Role.VIEWER`, todas as escritas usam `Role.MEMBER`.
- **Custo O(n) e teto configurável:** cada commit (incluindo merge 3-way e revert) materializa todos os `n` itens em `dataset_commit_items` (custo O(n) em tempo e armazenamento). Adicionado `Settings.max_dataset_commit_items` (`MATRIX_MAX_DATASET_COMMIT_ITEMS`, padrão `10_000`), aplicado em `commit_changes`, `merge_branches` e `revert_branch`, retornando `422` (`CommitItemLimitError`) quando excedido.
- **Vínculo `runs.dataset_version_id`:** `POST /api/v1/runs` valida `dataset_version_id`:
  - Sempre bloqueia (`422`) referências a `DatasetCommit` ou `Dataset` de outro projeto (anti-IDOR).
  - Quando a referência existe como `DatasetCommit` ou `Dataset` do projeto, materializa a aresta `trained_on` em `mlops_artifact_lineage`.
  - Quando a referência tem formato de UUID do MATRIX (32 hex) ou o projeto já possui commits de dataset e o ID não existe no projeto, retorna `422`.
  - Referências curtas opacas em projetos sem commits de dataset (ex.: `"dv1"` em `tests/test_runs_api.py` do G1) seguem aceitas para não quebrar a suíte G1 existente.

### 4. Fragmento de migração (`server/migrations/fragments/t6_registry.py`)
- Cria `model_stage_history`, `model_webhooks`, `webhook_deliveries`, o índice único parcial `uq_mlops_model_versions_one_production` (arquivando versões `production` duplicadas mais antigas antes de criar o índice) e os índices de travessia de linhagem (`ix_runs_project_dataset_version`, `ix_runs_project_parent_run`, `ix_mlops_mv_project_eval_exp`, `ix_mlops_mv_project_safety_scan`, `ix_benchmark_runs_project_ds_commit`).
- Nenhum arquivo criado em `server/migrations/versions/` (conforme HANDOFF §4).

## Adiado (fora do escopo de T6)
- `/api/v1/data-sets` por manifesto de arquivos e adaptadores somente-leitura LakeFS/DVC (contrato G3 §2) — adiado conforme especificação da T6.
- Resolução de versão por `@alias` na rota `GET /models/{name}/versions/{version|@alias}` e comparação/UI React Flow ficam para T9/T10.
