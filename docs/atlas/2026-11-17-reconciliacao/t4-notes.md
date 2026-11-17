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

## Estado

(atualizado a cada etapa)

## Ainda aberto (T5–T10)

