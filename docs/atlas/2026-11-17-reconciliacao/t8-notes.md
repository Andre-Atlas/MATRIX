# T8 — Gateway, Safety (Red-Team) e Vetores (notas de trabalho)

Branch `ccr-9443eae6-fk5867`. Base: ADR-011, ADR-009, ADR-013, `HANDOFF.md`, `tasks/T8-gateway-safety-vectors.md`.

## Decisões

### 1. AI Gateway (`gateway/`, `api/gateway.py`)
- **Sem mock silencioso.** `mock_fallback_on_no_key=True` foi removido de `OpenAIProvider` e `AnthropicProvider`, e `"mock"` saiu da ordem padrão de fallback (`routing_order = ["openai", "anthropic"]`).
  - Quando faltam as chaves de API do operador (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), o endpoint responde `503` com `detail.code == "provider_not_configured"` e `missing_env_vars` listando apenas os nomes das variáveis esperadas (nunca valores).
  - O provedor `mock` só executa quando o cliente solicita explicitamente `provider: "mock"` (ou quando configurado manualmente em `routing_order` em testes unitários). Toda resposta simulada — inclusive cache hits de respostas simuladas — traz `x_matrix.simulated: true` no corpo, cabeçalho `x-matrix-simulated: true` e `simulated=True` na tabela `gateway_requests`.
- **Isolamento estrito por projeto.**
  - `compute_cache_key` e `GatewayCache` incluem `project_id` na chave SHA-256 (exata e semântica) e mantêm contadores por projeto.
  - `GET /stats`, `GET /providers`, `GET /rules` e `GET /logs` filtram estritamente por `principal.project_id` (métricas de provedores são agregadas por projeto a partir de `gateway_requests`; regras refletem o `rate_limit_rpm` do projeto e não incluem `mock` na cadeia de produção).
- **Estado compartilhado no banco (`gateway/models.py`).**
  - `gateway_rate_windows(project_id, scope, window_start, request_count, token_count)` com chave primária composta `(project_id, scope, window_start)` e janela fixa de 60 s.
  - `GatewayRateLimiter.acquire()` faz upsert atômico (`INSERT ... ON CONFLICT DO UPDATE ... RETURNING` em PostgreSQL e SQLite) **antes** de chamar o provedor, fazendo `rollback` se exceder RPM ou TPM; após a chamada, `reconcile_tokens()` ajusta `token_count` para o uso real (ou `0` em cache hit/erro).
  - `gateway_requests(project_id, ts, route, model, provider, status, latency_ms, input_tokens, output_tokens, cost_usd, cache_hit, simulated, error_sanitized)` persiste logs e alimenta `GET /stats`, `GET /logs` e `GET /providers`.
  - Limpeza oportunista limitada (`cleanup_expired_requests`, `cleanup_expired_windows`) com retenção configurável (`retention_days=30`, `cleanup_batch_size=500`).
  - O `get_gateway` em `api/gateway.py` vincula `request.app.state.session_factory` ao `GatewayRouter` sem precisar alterar `app.py`.
  - **Cache de prompt:** permanece em memória por processo (documentado em `gateway/cache.py`), com chave isolada por `project_id`.
- **Allowlist de parâmetros, tetos e streaming.**
  - Allowlist explícita por provedor (`OPENAI_ALLOWED_PARAMS`, `ANTHROPIC_ALLOWED_PARAMS`, `MOCK_ALLOWED_PARAMS`); parâmetros desconhecidos ou proibidos (`base_url`, `api_key`, etc.) são descartados e reportados no cabeçalho `x-matrix-warnings`.
  - Tetos configuráveis em `GatewayRouter`: `max_tokens_cap=8192`, `max_n_cap=4` (exceder retorna `422`).
  - `stream: true` retorna `422` com `code: "stream_not_supported"`.
- **Segredos e sanitização de erros.**
  - Chaves e `base_url` são resolvidos exclusivamente de variáveis de ambiente do operador (`OPENAI_API_KEY`/`OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`), nunca do corpo da requisição.
  - `sanitize_provider_error` remove segredos (`Bearer`, `x-api-key`, `sk-*` e valores explícitos), redige URLs completas (`[redacted-url]`) e limita o corpo do provedor a 200 caracteres.

### 2. Safety / Red-Team (`safety/`, `api/safety.py`)
- Removida a resposta defensiva fixa (`default_defensive_response`) que gerava falso positivo de score 100 sem alvo.
- Sem `simulated_outputs` (não vazio) e sem `response_generator`: `run_safety_scan` retorna `status="not_evaluated"`, `global_score=None`, `simulated=False`, `message` explícita e `findings=[]`.
- Com `simulated_outputs` fornecidos pelo cliente: avalia apenas as sondas com saída fornecida (sem fabricar respostas para as demais) e marca `simulated=True`.
- Com `response_generator` real (sem `simulated_outputs`): avalia todas as sondas e marca `simulated=False`.
- `api/safety.py` persiste `simulated` e `global_score` (nullable) em `SafetyScan` e expõe `simulated`, `global_score`, `safety_score` (nullable), `risk_level="not_evaluated"` e `message` nas respostas.

### 3. Vetores (`vectors/`, `api/vectors.py`)
- Respostas de `/api/v1/vectors/projection` e `/api/v1/vectors/drift` rotulam explicitamente `engine: "pca-lite"`, `embedding: "hash-3gram (demo)"`, `simulated: true` (no topo e em `metadata`; modo sintético de drift mudou de `"computed"` para `"demo"`).
- Interface `ProjectionEngine` e exceção `EngineUnavailable` definidas em `vectors/projection.py` com implementação `PCALiteEngine` e stubs `UMAPProjectionEngine` e `HDBSCANClusterEngine` (que levantam `EngineUnavailable` conforme ADR-009).

### 4. Migração (fragmento)
- `server/migrations/fragments/t8_gateway_safety_vectors.py` cria `gateway_rate_windows` e `gateway_requests`, adiciona `safety_scans.simulated` (`Boolean`, default `false`) e altera `safety_scans.global_score` para `nullable=True` (com `downgrade()` seguro preenchendo `NULL`s antes de restaurar `NOT NULL`).
- Validado por `server/tests/test_t8_migration.py` contra o esquema 0008 via `alembic.autogenerate.compare_metadata`.

## Justificativa das atualizações de testes existentes
Nenhum teste foi apagado. As asserções abaixo foram atualizadas para refletir o contrato T8:
- `tests/test_ai_gateway.py`: `test_gateway_api_auth_rbac`, `test_gateway_api_chat_completions_caching`, `test_gateway_api_rate_limiting`, `test_gateway_api_stats_providers_and_logs` agora enviam `"provider": "mock"` explicitamente (antes dependiam do fallback silencioso de mock sem chave) e verificam `x-matrix-simulated: true` / `simulated=True`.
- `tests/test_safety_scanner.py`: `test_run_safety_scan_defaults` agora afirma `status == "not_evaluated"` e `global_score is None` (antes afirmava score 100 fabricado sem alvo); `test_run_safety_scan_with_target_prompt` e `test_api_scans_rbac_permissions` passam `simulated_outputs` para o caso avaliado (`simulated=True`) e verificam também o caso sem alvo (`not_evaluated`).

## Itens adiados (fora do escopo de T8)
- **Chaves virtuais (`mxg_…`) e orçamentos por chave virtual** (ADR-011) — requer modelo de chaves virtuais e serviço dedicado `matrix-gateway`.
- **Streaming SSE** (`/chat/completions` com `stream: true`) — proxy sem buffer e medição de TTFT (ADR-011).
- **Cache de prompt compartilhado (Redis / semântico por embedding real)** — atualmente em memória por processo, isolado por `project_id`.
- **Circuit breaker compartilhado entre workers** — atualmente em memória por processo (rate limit e logs já são compartilhados em DB).
- **Motores analíticos reais (`umap-learn`, `hdbscan`, `numpy`/`scipy`)** — rodam no worker via extra `analytics` (ADR-009).
- **Alvo real de Safety via gateway/egress** — `run_safety_scan` já aceita `response_generator` (`simulated=False`), aguardando integração de endpoint alvo via `egress_client`.

## Arquivos criados e modificados
- Criados:
  - `server/src/matrix_server/gateway/models.py`
  - `server/migrations/fragments/t8_gateway_safety_vectors.py`
  - `server/tests/test_t8_migration.py`
  - `docs/atlas/2026-11-17-reconciliacao/t8-notes.md`
- Modificados:
  - `server/src/matrix_server/gateway/__init__.py`
  - `server/src/matrix_server/gateway/cache.py`
  - `server/src/matrix_server/gateway/rate_limiter.py`
  - `server/src/matrix_server/gateway/router.py`
  - `server/src/matrix_server/api/gateway.py`
  - `server/src/matrix_server/safety/runner.py`
  - `server/src/matrix_server/api/safety.py`
  - `server/src/matrix_server/vectors/__init__.py`
  - `server/src/matrix_server/vectors/projection.py`
  - `server/src/matrix_server/vectors/drift.py`
  - `server/src/matrix_server/api/vectors.py`
  - `server/tests/test_ai_gateway.py`
  - `server/tests/test_safety_scanner.py`
  - `server/tests/test_vectors_drift.py`
