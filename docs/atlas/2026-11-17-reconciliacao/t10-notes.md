# T10 — SDK Python: unificação de `mlops`, proxy/TLS e Registry/Linhagem canônicos (notas de trabalho)

Branch `ccr-9443eae6-fk5867`. Base: T4 (`t4-notes.md`), T5 (`t5-notes.md`), T6 (`t6-notes.md`), ADR-013.

## Decisões tomadas

### 1. Unificação de `sdk/python/src/matrix_sdk/mlops.py`
- `TrainingRun` passou a ser um wrapper fino sobre `matrix_sdk.tracking.Run` (sem segunda pilha HTTP, sem segunda fila/thread de envio, sem segunda implementação de telemetria — `start_system_monitor` delega para `matrix_sdk.system.SystemMonitor`).
- `MLOpsClient` passou a ser uma camada de compatibilidade depreciada emitindo `DeprecationWarning` em todos os métodos legados (`start_run`, `create_sweep`, `run_sweep_agent`, `register_model`, `create_model_version`, `set_model_alias`, `download_model`):
  - `start_run` → `matrix_sdk.tracking.start_run` (`/api/v1/runs`)
  - `TrainingRun.log` / `finish` / `log_artifact` / `log_classic_eval` → `Run.log` / `Run.finish` / `Run.log_artifact` (`/api/v1/artifacts` + `/api/v1/runs/{id}/artifacts`) / `Run.log_report("custom", "classic_eval", ...)` (`/api/v1/runs/{id}/reports`).
  - `create_sweep` / `run_sweep_agent` → `matrix_sdk.sweeps.sweep` (`/api/v1/sweeps`) e `matrix_sdk.sweeps.agent` (`/api/v1/sweeps/{id}/next` + `/report`), traduzindo `bayes` → `tpe`, `categorical` → `choice`, `early_stopping` → `early_stop`.
  - `register_model` / `create_model_version` / `set_model_alias` / `download_model` → `client.registry.*`.
- Nenhuma chamada no SDK atinge as rotas depreciadas `/api/v1/mlops/runs*`, `/api/v1/mlops/artifacts*` ou `/api/v1/mlops/sweeps*`.

### 2. Proxy e TLS (`trust_env` / `_net.client_kwargs`)
- Auditados todos os usos de `httpx.Client` / `httpx.AsyncClient` em `sdk/python/src/matrix_sdk/`:
  - `client.py`: já usa `**client_kwargs(transport)`.
  - `registry.py` (downloads de URLs pré-assinadas externas em `RegistryClient.download`): usa `httpx.Client(timeout=_STREAM_TIMEOUT, **client_kwargs())`, respeitando `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY` (inclusive IPv6 `[::1]`) e `SSL_CERT_FILE`/`SSL_CERT_DIR` via `httpx.create_ssl_context(trust_env=True)`, nunca usando `verify=False` e nunca enviando o cabeçalho `Authorization` do MATRIX para URLs externas.
  - `mlops.py`: não instancia mais `httpx.Client` diretamente.

### 3. Registry e Linhagem canônicos (`sdk/python/src/matrix_sdk/registry.py`)
- Criados `RegistryClient` (`client.registry`), `WebhooksClient` (`client.registry.webhooks`) e funções de topo em `matrix_sdk`: `log_model`, `register`, `promote`, `set_alias`, `lineage` (além de `client.lineage(...)` e `client.register_model(...)` roteado diretamente para `client.registry.register`).
- Rotas alvo alinhadas com `server/src/matrix_server/api/mlops.py` (T6):
  - CRUD de modelos e versões (router não-depreciado `/api/v1/mlops`): `POST/GET/DELETE /api/v1/mlops/models*`, `POST /api/v1/mlops/models/{id}/versions`.
  - Rotas canônicas T6 (`/api/v1`): `POST /api/v1/models/{id}/versions/{v}/stage`, `PUT/DELETE /api/v1/models/{id}/aliases/{alias}`, `GET /api/v1/models/{id}/history`, `GET /api/v1/models/{id}/versions/{v}/files`, `POST/GET/DELETE /api/v1/models/{id}/webhooks*`, `GET /api/v1/models/{id}/webhooks/{wid}/deliveries`, `GET /api/v1/lineage`.
- `log_model(path, name, ...)` envia o artefato para o store canônico `POST /api/v1/artifacts`, vincula ao run ativo (ou `run_id`) via `POST /api/v1/runs/{id}/artifacts`, registra o modelo (reaproveitando o existente em caso de `409`) e cria nova versão.

### 4. Remoção de `api/mlops_compat.py` (coordenação)
- Conforme coordenação com T9 em paralelo, `server/` não foi alterado nesta etapa; a coordenação fará o inline de `mlops/compat.py` na migração 0008 e removerá `api/mlops_compat.py` após o encerramento de T9 e T10.

## Arquivos criados e alterados

### Criados
- `sdk/python/src/matrix_sdk/registry.py`
- `sdk/python/tests/test_registry_sdk.py`
- `docs/atlas/2026-11-17-reconciliacao/t10-notes.md`

### Alterados
- `sdk/python/src/matrix_sdk/mlops.py`
- `sdk/python/src/matrix_sdk/client.py`
- `sdk/python/src/matrix_sdk/__init__.py`
- `sdk/python/tests/test_mlops_sdk.py`
- `sdk/python/tests/test_sweeps_sdk.py`

## Verificação
- `cd sdk/python && uv run ruff check .` → `All checks passed!`
- `cd sdk/python && uv run pytest -q` → **121 passed** (base anterior: 114; +7 novos testes em `test_registry_sdk.py`, incluindo execução de `examples/train_sklearn.py` e `examples/train_pytorch.py`, depreciação, proxy/TLS, registry, webhooks e linhagem limitada).
