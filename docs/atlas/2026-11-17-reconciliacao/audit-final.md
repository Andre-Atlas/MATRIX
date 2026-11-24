# Auditoria Final T12 — Reconciliação (`feat/reconciliacao-t5-t13`) — 2026-11-24

Escopo: `/Users/andreatlas/MATRIX` · branch `feat/reconciliacao-t5-t13` · diff `origin/main..HEAD` (`2b00014..4280016`, 190 arquivos alterados, `+19137/-4996`) · Dimensões: `segurança, falhas-silenciosas, performance, testes`
Resumo: 5 CRITICAL, 17 HIGH, 23 MEDIUM, 4 LOW (0 não confirmados; 2 fusões de duplicatas aplicadas: `sync_exec=True` HIGH [segurança + performance] e `httpx.AsyncClient` em `gateway/router.py` MEDIUM [segurança + performance])

> **Nota de contrato (`HANDOFF.md` & `plan.md`):** Motores explicitamente rotulados com `simulated=true` (`LocalSimBenchmarkEngine`, `FakeSandbox` em `dev`/`test`, `MockProvider` explícito em Gateway, `PCALiteEngine` em Vetores) são intencionais nesta etapa. O que **não** é aceito é mock silencioso quando o backend falha ou retorno sintético marcado com `simulated=false`.

---

## Achados

### CRITICAL — Exfiltração arbitrária de `os.environ` e oráculo de presença via `runner_config.api_key_env` em benchmarks
Dimensões: `segurança` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/benchmarks/runner.py:26`: `API_KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")`
- `server/src/matrix_server/benchmarks/runner.py:94-99`: `validate_runner_config` aceita qualquer identificador em maiúsculas sem lista de permissão ou prefixo dedicado.
- `server/src/matrix_server/benchmarks/runner.py:290-296, 340, 494`: `execute_http_runner_tasks` lê `os.environ.get(api_key_env)` **antes** de acionar `egress_client.post(...)` e injeta o valor em `Authorization: Bearer {secret_val}`; se ausente, levanta `RuntimeError(f"Environment variable '{api_key_env}' is not set on the server")`, gravado em `BenchmarkRun.error` (`failed_run.error = str(exc)[:2000]`).
- `server/src/matrix_server/api/benchmarks.py:163, 236`: qualquer `MEMBER` do projeto pode submeter `runner_config.api_key_env` via `POST /api/v1/benchmarks/runs`.
Impacto: Qualquer membro de projeto tem um oráculo determinístico de existência para qualquer variável de ambiente do servidor (mesmo com egress totalmente bloqueado) e, havendo qualquer host na allowlist de egress (ou modo público), consegue exfiltrar `MATRIX_SECRET_KEY`, `DATABASE_URL`, `MATRIX_ADMIN_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` ou credenciais S3 no header `Authorization`.
Correção sugerida: Restringir `api_key_env` a um prefixo/allowlist controlado pelo operador (ex.: `MATRIX_BENCHMARK_KEY_*` ou lista configurada em `Settings`), validar antes de persistir, e executar a checagem de egress antes de resolver segredos sem expor o nome/presença de variáveis do sistema no campo `error`.

### CRITICAL — Fallback silencioso para `MOCK_GATEWAY_*` mascara falhas de backend e engole `NEXT_REDIRECT` em `gateway/page.tsx`
Dimensões: `falhas-silenciosas` · Status: **confirmado**
Evidência:
- `apps/web/src/app/(app)/gateway/page.tsx:20-48`: pré-inicializa `stats`, `providers`, `logs` e `rules` com `MOCK_GATEWAY_*`, envolve `Promise.all([apiGet(.../stats), apiGet(.../providers), apiGet(.../logs), apiGet(.../rules).catch(...)])` em `try/catch` e no `catch` apenas loga `console.warn(...)` e marca `isSimulated = true`.
- `apps/web/src/lib/api.ts:51-69, 80-99`: `apiGet` chama `redirect("/login")`, `redirect("/projects")` ou `redirect("/api/auth/expired?...")` (que lançam `NEXT_REDIRECT`). Em navegação client-side no App Router (onde `(app)/layout.tsx` não re-executa), a expiração de sessão ou projeto stale faz `apiGet` lançar `NEXT_REDIRECT` dentro do `try/catch` da página.
Impacto: Viola o contrato contra mock silencioso: qualquer erro 5xx ou indisponibilidade da API renderiza telemetria falsa (`MOCK_GATEWAY_*`), e sessões expiradas em navegação client-side têm o `NEXT_REDIRECT` engolido e enxergam dados simulados do dashboard em vez de redirecionar ao login.
Correção sugerida: Remover os fallbacks `MOCK_GATEWAY_*` de `gateway/page.tsx`, chamar `unstable_rethrow(error)` antes de qualquer tratamento, e deixar erros reais propagarem para `apps/web/src/app/(app)/gateway/error.tsx` (mantendo mocks exclusivamente sob `MATRIX_MOCK === "1"` em `mock-api.ts`).

### CRITICAL — Jobs `mlops.evaluate` enfileirados sem `benchmark_run_id` são descartados silenciosamente e marcados `done`
Dimensões: `falhas-silenciosas` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/api/mlops.py:1339-1373`: `POST /api/v1/mlops/models/{model_id}/versions/{version}/evaluate` enfileira o job `"mlops.evaluate"` com payload contendo `model_id`, `model_name`, `version`, `model_version_id`, `dataset_id`, `name`, `metrics` — sem criar `BenchmarkRun` e sem enviar `benchmark_run_id`.
- `server/src/matrix_server/workers/handlers.py:128-137`: `run_benchmark_worker_handler` (registrado para `"benchmark.run"` e `"mlops.evaluate"`) verifica `if not benchmark_run_id: log.warning(...); return`.
- `server/src/matrix_server/workers/runner.py:141-146`: o retorno normal do handler leva `jobs.mark_done(session, job.id, worker_id)` e `session.commit()`.
Impacto: Toda chamada ao endpoint de avaliação de versão de modelo retorna HTTP 202 `queued`, o worker descarta o trabalho sem executar qualquer avaliação e ainda marca o job como `done`.
Correção sugerida: Criar o `BenchmarkRun` correspondente ao enfileirar em `evaluate_model_version` (incluindo `benchmark_run_id` no payload) ou separar o handler de `mlops.evaluate`, e fazer o worker falhar explicitamente (`retry=False` / exceção permanente) caso `benchmark_run_id` esteja ausente no payload.

### CRITICAL — I/O síncrono de banco e até 500 `DELETE`s individuais por limpeza no event loop `async` do AI Gateway
Dimensões: `performance` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/api/gateway.py:130-150`: `chat_completions` é `async def` (executa diretamente no event loop principal do asyncio/uvicorn).
- `server/src/matrix_server/gateway/router.py:590, 651, 679, 693, 772, 780, 808, 819, 847, 898-945`: `route_chat_completion` (`async def`) invoca diretamente sessões SQLAlchemy síncronas (`rate_limiter.acquire`, `reconcile_tokens`, `_record_log`, `cleanup_expired_requests`).
- `server/src/matrix_server/gateway/rate_limiter.py:47-48, 84-140, 191-212, 220-263`: `_acquire_db`, `reconcile_tokens` e `_maybe_cleanup_db` fazem commits síncronos bloqueantes no event loop; a cada `cleanup_interval=100` chamadas, `cleanup_expired_windows` seleciona até `cleanup_batch_size=500` chaves e executa um loop de até 500 `DELETE`s individuais (`lines 253-261`) antes do `db.commit()`.
Impacto: Sob tráfego concorrente ou latência de PostgreSQL, todo o processo FastAPI fica bloqueado no event loop durante queries/commits e principalmente durante rajadas de 500 `DELETE`s sequenciais por limpeza.
Correção sugerida: Executar as operações síncronas de DB fora do event loop (via `starlette.concurrency.run_in_threadpool` ou driver async/worker assíncrono) e substituir o loop de 500 `DELETE`s por um único `DELETE` em lote (`tuple_(...).in_(...)` ou subselect limitado).

### CRITICAL — Full-table scan sem limite temporal/linhas e ordenação em Python em `get_stats` e `get_providers_status`
Dimensões: `performance` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/gateway/router.py:954-969`: `get_stats` executa `select(GatewayRequest).where(GatewayRequest.project_id == project_id)` sem `.limit()` e sem janela temporal (mesmo `fallback_events_24h` soma todas as linhas dos últimos 30 dias), materializa todos os objetos ORM em memória e executa `sorted(r.latency_ms for r in rows)` em Python.
- `server/src/matrix_server/gateway/router.py:1012-1020`: `get_providers_status` executa outro `select(GatewayRequest).where(GatewayRequest.project_id == project_id)` ilimitado apenas para somar contagens e latências por provedor em Python.
- `apps/web/src/app/(app)/gateway/page.tsx:27-30`: toda renderização SSR de `/gateway` dispara `/stats` e `/providers` simultaneamente.
Impacto: Conforme `gateway_requests` cresce (até `retention_days=30`), cada abertura do painel `/gateway` ou consulta de telemetria carrega todas as linhas ORM do projeto em RAM e ordena na CPU do servidor, degradando até OOM/timeout.
Correção sugerida: Substituir ambos os scans por agregações SQL (`COUNT`, `SUM`, `AVG`, filtro `ts >= now - 24h` onde aplicável, e `percentile_disc`/`percentile_cont` no PostgreSQL ou amostra limitada recente para percentis).

---

### HIGH — Envenenamento cross-tenant de Circuit Breaker (DoS) e vazamento de estado global no AI Gateway
Dimensões: `segurança` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/app.py:54`: cria uma única instância global `app.state.gateway_router = GatewayRouter()`.
- `server/src/matrix_server/gateway/router.py:162-179, 337, 341-344, 421, 455-458, 720-724, 800-805`: cada provedor (`openai`, `anthropic`) possui um único `CircuitBreaker` global; `OpenAIProvider`/`AnthropicProvider` chamam `resp.raise_for_status()`, convertendo qualquer erro 4xx induzido pelo payload (ex.: `model` inválido) em `ProviderExecutionError`, o que chama `prov.circuit_breaker.record_failure()` (`line 801`). Após 3 falhas (`failure_threshold=3`), o breaker abre globalmente e bloqueia todos os outros projetos (`lines 720-724`).
- `server/src/matrix_server/gateway/router.py:207-208, 950-952, 1030` e `server/src/matrix_server/api/gateway.py:175-181`: `GET /api/v1/gateway/providers` e `GET /api/v1/gateway/stats` expõem `circuit_breaker`, `failure_count` e `healthy_providers` globais entre tenants.
Impacto: Um usuário `MEMBER` de um projeto malicioso ou defeituoso pode abrir o circuit breaker de `openai`/`anthropic` para todos os demais tenants da plataforma (DoS cross-tenant) e observar estado global de falhas de outros projetos.
Correção sugerida: Isolar o estado do `CircuitBreaker` por `(project_id, provider)` (com eviction limitada) e não contabilizar respostas HTTP 4xx causadas por requisição inválida do cliente (400/404/422) como falha de disponibilidade do provedor; escopar `healthy_providers` e status do breaker ao projeto consultado.

### HIGH — `sync_exec=True` sem guarda de ambiente em `POST /api/v1/benchmarks/runs` permite DoS e HTTP 500 não tratado
Dimensões: `segurança, performance` *(fusão de Segurança + Performance)* · Status: **confirmado**
Evidência:
- `server/src/matrix_server/api/benchmarks.py:164-168, 250-264`: aceita `sync_exec=True` via body ou query param sem verificar `is_test_env()` (diferente de `server/src/matrix_server/api/sandboxes.py:263`, que exige `is_test_env()`), chamando `run_benchmark_job(db, run.id)` diretamente no handler HTTP.
- `server/src/matrix_server/benchmarks/runner.py:287, 305-340, 496`: permite `timeout_seconds` de até `3600.0`s por tarefa em loop sequencial (`for task in task_defs`), segurando worker HTTP e conexão/sessão DB pelo tempo total; ao falhar, `run_benchmark_job` faz `raise` na linha `496` sem tratamento em `create_run`, devolvendo HTTP 500.
Impacto: Em produção, qualquer `MEMBER` pode travar threads HTTP e conexões do pool por horas enviando `sync_exec=true` com backend `http`/`vllm` e múltiplas tarefas, ou provocar HTTP 500 não tratado em qualquer falha de runner síncrono.
Correção sugerida: Restringir `sync_exec=True` exclusivamente a ambiente de teste (`is_test_env()`, ou rejeitar/ignorar como em `sandboxes.py:263`), impor teto seguro de timeout total/concorrência e tratar falhas esperadas sem derrubar o endpoint com 500.

### HIGH — `EngineUnavailable` (e `EgressError`) em `run_benchmark_job` gera HTTP 500 síncrono e retries/status `done` inconsistentes no worker
Dimensões: `falhas-silenciosas` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/benchmarks/runner.py:439, 485-496`: ao capturar `EngineUnavailable` ou `EgressError`, `run_benchmark_job` grava `failed_run.status = "failed"` e `failed_run.error` no banco e depois executa `raise` (`line 496`).
- `server/src/matrix_server/api/benchmarks.py:252`: `create_run(..., sync_exec=True)` não captura exceções de `run_benchmark_job`, propagando `EngineUnavailable`/`EgressError` como HTTP 500.
- `server/src/matrix_server/workers/runner.py:144, 157`: o worker captura o `raise` e chama `jobs.fail_status(session_factory, job.id, detail, retry=True, ...)`, agendando retry com backoff exponencial; na segunda tentativa, `run_benchmark_job:439` encontra `run.status == "failed"`, retorna normalmente e `workers/runner.py:144` marca o `Job` como `"done"` apesar do benchmark ter falhado.
Impacto: Motores stub (`lm_eval`, `vlmeval`) e bloqueios de egress causam 500 em chamadas síncronas e ciclos inúteis de retry seguidos por estado inconsistente (`Job.status == "done"` para `BenchmarkRun.status == "failed"`).
Correção sugerida: Tratar `EngineUnavailable` e `EgressError` permanentes sem retry no worker (ou propagar exceção não-retentável que leve `jobs.fail_status(..., retry=False)`), e em execução síncrona mapear `EngineUnavailable` para HTTP 501/422 (ou retornar o run `failed` conforme contrato) sem 500.

### HIGH — `HttpBenchmarkEngine` sintetiza score falso via SHA-256 da resposta HTTP mantendo `simulated=False`
Dimensões: `falhas-silenciosas` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/benchmarks/runner.py:157-161`: `HttpBenchmarkEngine.simulated = False`.
- `server/src/matrix_server/benchmarks/runner.py:347-372, 479`: envia um prompt fixo (`"Evaluate benchmark task {task.name}"`), ignora o conteúdo semântico da resposta do modelo e calcula `primary_score` a partir dos primeiros 8 dígitos hexadecimais de `hashlib.sha256(response.content).hexdigest()` (entre `0.50` e `0.949`), gravando `BenchmarkTaskResult.simulated = False` e `BenchmarkRun.simulated = False`.
Impacto: Viola diretamente a regra do projeto contra resultados sintéticos sem rótulo `simulated=true`: usuários recebem scores pseudocientíficos e não reprodutíveis em `simulated=false` ao avaliar modelos HTTP/vLLM.
Correção sugerida: Enquanto não houver parser/dataset avaliador real para `http`/`vllm`, ou levantar `EngineUnavailable` explícito, ou marcar `HttpBenchmarkEngine.simulated = True` (com aviso explícito de que o score ainda é sintético).

### HIGH — `AttributeError` em `Sweep.method` derruba linhagem com HTTP 500 enquanto a UI engole o erro como grafo vazio
Dimensões: `falhas-silenciosas` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/sweeps/models.py:33-58`: o modelo `Sweep` armazena `method` dentro da coluna JSON `config`, não possui coluna/atributo `Sweep.method`.
- `server/src/matrix_server/mlops/lineage.py:271-272, 568-572`: `_fetch_hop_edges` inclui `("sweep", r.sweep_id)` via `Run.sweep_id`, e `_enrich_nodes` acessa `meta = {"method": sw.method, "status": sw.status}`, disparando `AttributeError: 'Sweep' object has no attribute 'method'` -> HTTP 500 sempre que qualquer grafo alcança um nó `sweep`.
- `apps/web/src/app/(app)/models/page.tsx:73, 80` e `apps/web/src/components/models/models-registry-client.tsx:156, 164`: usam `.catch(() => null)` / `.catch(() => ({ model_id: modelId, nodes: [], edges: [] }))`.
Impacto: Qualquer consulta de linhagem envolvendo um `Sweep` (direta ou via `model_version <- run -> sweep`) quebra no backend com 500, e a interface de Model Registry mascara o 500 exibindo um grafo vazio sem erro.
Correção sugerida: Alterar `mlops/lineage.py:572` para `(sw.config or {}).get("method")` (com teste cobrindo linhagem de `sweep` e `model_version` originada de sweep) e exibir estado de erro explícito na aba/SSR de linhagem em vez de silenciar falhas.

### HIGH — Falha ao carregar detalhes de Safety ou ausência de score fabrica `"Compliant"` (100%) / `"Low Risk"`
Dimensões: `falhas-silenciosas` · Status: **confirmado**
Evidência:
- `apps/web/src/app/(app)/safety/page.tsx:24-31`: `try { rawReport = await apiGet(...) } catch { rawReport = null; }` engole erros (inclusive `NEXT_REDIRECT` na chamada secundária) e entrega `allFindings = []` ao cliente.
- `apps/web/src/lib/safety.ts:196-205`: em `summarizeScan`, quando `catFindings.length === 0` (ex.: detalhes não carregados) e `testedSet` está vazio ou contém a categoria, assume `passedCount = catProbesTested`, `passRate = 1` e `status = "compliant"`.
- `apps/web/src/lib/safety.ts:23, 223-236, 288-303`: `calculateSafetyScore(0, 0, [])` retorna `100`; `adaptSafetyScan` verifica `(s?.safety_score === null && s?.global_score === null)` (falha quando apenas um campo está presente ou quando o outro é `undefined`) e cai para `100` / `"low"`.
- `apps/web/src/lib/safety.ts:440-481`: ramos `default` de `formatRiskLevel` e `formatComplianceStatus` retornam `"Low Risk"` e `"Compliant"`.
Impacto: Um erro ao carregar os achados de um scan de segurança ou um scan sem score exibe falsamente conformidade total (`100%`, `"Compliant"`, `"Low Risk"`).
Correção sugerida: Propagar erros do detalhe em `safety/page.tsx` (com `unstable_rethrow`), usar `== null` para detectar score ausente em `adaptSafetyScan` e tratar `findings` vazios (em scan não avaliado ou erro de carregamento) e ramos `default` como `"not_evaluated"`/`"unknown"` — nunca `100%`/`"Compliant"`.

### HIGH — `try/catch` em Server Components de `(app)` engole `NEXT_REDIRECT` em sessões expiradas / projetos stale
Dimensões: `falhas-silenciosas` · Status: **confirmado**
Evidência:
- `apps/web/src/lib/api.ts:51-69, 80-99`: `apiGet` invoca `redirect(...)` (lança `NEXT_REDIRECT`) ao receber `401` ou `404 "project not found"` ou quando falta sessão.
- Em navegação client-side (`<Link>` dentro de `(app)`), o Next.js pode renderizar apenas o `page.tsx` sem re-executar `apps/web/src/app/(app)/layout.tsx`.
- Os blocos `try { ... apiGet(...) } catch (err) { initialError = ... }` em `apps/web/src/app/(app)/models/page.tsx:27-39`, `training/page.tsx:37-47`, `sweeps/page.tsx:11-16`, `sweeps/[sweepId]/page.tsx:17-24`, `lineage/page.tsx:28-37`, `benchmarks/page.tsx:16-31` (além de `sandboxes/page.tsx:16-22, 32-39` e os catches secundários de `safety/page.tsx:24-31` e `datasets/[datasetId]/page.tsx:19-36`) não chamam `unstable_rethrow(err)` (diferente apenas de `isNotFound(err)`).
Impacto: Com sessão expirada durante navegação client-side, o usuário não é redirecionado para `/api/auth/expired` / `/login` / `/projects?stale=1`; vê páginas vazias com mensagem técnica `"NEXT_REDIRECT"` no banner de erro.
Correção sugerida: Importar e chamar `unstable_rethrow(err)` (de `next/navigation`) no topo de todo bloco `catch` em Server Components que chamam `apiGet`/`apiSend` (ou centralizar num helper seguro).

### HIGH — Scorer desconhecido de sandbox passa com `1.0` e falhas de `ArtifactStore` são suprimidas em `log.debug`
Dimensões: `falhas-silenciosas` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/sandboxes/runner.py:861-864`: ramo `else:` para `scorer_name` desconhecido define `status = "passed"` e `score = 1.0`.
- `server/src/matrix_server/sandboxes/runner.py:854-855`: `llm_judge` sem `expected_output` assume `float(scorer_config.get("score", 1.0))` (`1.0` por padrão).
- `server/src/matrix_server/sandboxes/runner.py:885-897`: se `save_blob(rel_path, data)` falha (ex.: cota excedida ou falha S3), registra apenas `log.debug(...)` e ainda adiciona o item em `verified_artifacts` com `"verified": True, "storage_uri": None`.
- `server/src/matrix_server/sandboxes/runner.py:933-939`: falha em `get_artifact_store()` é suprimida em `log.debug(...)`, executando o job sem persistência de artefatos.
Impacto: Erros de digitação no nome do scorer aprovam tarefas inválidas com nota máxima; falhas de armazenamento/cota de artefatos passam despercebidas em produção reportando `"verified": True`.
Correção sugerida: Validar `scorer_name` contra allowlist conhecida na API (`422`) e falhar fechado no runner (`status="failed", score=0.0`); exigir `expected_output` explícito ou implementação real para `llm_judge`; registrar erro/marcar `verified=False` (ou falhar tarefa) quando `ArtifactStore` falhar em vez de suprimir em `log.debug`.

### HIGH — Lock `FOR UPDATE` em `next_trial` mantido durante leitura ilimitada, ClickHouse I/O, TPE `O(n²)` e shuffle de 100k grid
Dimensões: `performance` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/sweeps/service.py:211-275`: adquire `get_sweep(..., for_update=is_pg)` (`SELECT ... FOR UPDATE` no PostgreSQL) no início de `next_trial` e mantém esse row lock enquanto executa `_scored_trials_for_tpe` (`lines 154-186`: carrega todos os `Run`s e `SweepRun`s do sweep + chamada HTTP `store.summaries(...)` ao ClickHouse), `suggest_next` e `runs_service.check_capacity` (dois `COUNT(*)` em `runs`).
- `server/src/matrix_server/sweeps/engine.py:458-484`: `_parzen_components` ordena `aug = sorted([*pts, mid])` e faz `aug.index(z)` dentro de `for z in pts` (`O(n²)` por dimensão, chamado repetidamente para `DEFAULT_TPE_CANDIDATES=24` candidatos).
- `server/src/matrix_server/sweeps/engine.py:12-13, 426-430`: quando `seed` está definido no grid, `_suggest_grid` materializa e embaralha `list(range(total))` (até `MAX_GRID_COMBINATIONS = 100_000` elementos) a cada requisição `/next`.
Impacto: Agentes concorrentes de sweep ficam serializados no lock de linha do Postgres esperando ClickHouse, KDE quadrático e alocações/shuffles de 100 mil inteiros a cada trial.
Correção sugerida: Ler runs/métricas e calcular a sugestão fora do lock longo (ou usar atualização atômica/CAS apenas na alocação de `next_index`), substituir `aug.index(z)` por `bisect` (`O(n log n)`) e usar permutação determinística `O(1)` (ex.: cifra Feistel/LCG sobre `total`) para grid com seed.

### HIGH — `report_metric` busca até 5000 pontos do ClickHouse para todos os peers por step e lê `Run`/`Sweep` sem `for_update`
Dimensões: `performance` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/api/sweeps.py:180-183`: busca `sweep` e `run` sem `with_for_update()`.
- `server/src/matrix_server/sweeps/service.py:319-336` + `server/src/matrix_server/runs/service.py:186`: chama `runs_service.ingest_points(run, ...)` (cujo contrato exige lock de linha em `Run` para atualizar `point_count` e `key_counts`) e atualiza `sweep.best_value`/`sweep.best_run_id` sem lock.
- `server/src/matrix_server/sweeps/service.py:368-391` + `runs/store.py:33`: para todo step reportado com `early_stop`, chama `store.read_series(sweep.project_id, peer_ids, [sweep.metric_key], max_points=MAX_MAX_POINTS)` (`5000` pontos por peer!) **antes** mesmo de `evaluate_pruning` verificar `if step < r0` (`sweeps/engine.py:742`).
Impacto: Cada step de treino gera leitura `O(peers * 5000)` no ClickHouse (mesmo antes do primeiro rung) e sofre race condition de lost-update em `Run.point_count`/`key_counts` e `Sweep.best_value`.
Correção sugerida: Adquirir `for_update` em `Run` (e ao atualizar `Sweep.best_value`), verificar se `step` atingiu um rung (`step >= r0`) antes de consultar peers e armazenar/consultar apenas valores por rung em `SweepRun` (ou sumário direcionado) em vez de transferir séries de 5000 pontos por step.

### HIGH — N+1 `SpanStore.get_trace` (até 400 queries sequenciais ao ClickHouse) na Vectors API e recomputação `O(Q*D)` em `identify_knowledge_gaps`
Dimensões: `performance` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/api/vectors.py:68-76`: `_extract_items_from_spans` lista até 100 traces e executa `store.get_trace(...)` em loop (100 queries ClickHouse por chamada a `/projection`).
- `server/src/matrix_server/api/vectors.py:245-268`: `get_vector_drift` lista até 200 traces de referência e 200 de produção e executa `store.get_trace(...)` para cada um (até 400 queries sequenciais em `storage/spans.py:745-767` por chamada a `/drift`).
- `server/src/matrix_server/vectors/projection.py:355-370`: dentro de `for q in queries: for d in documents:`, recalcula `d.embedding or deterministic_text_embedding(d.text)` a cada par `(q, d)` (`O(Q * D * len(text))`), já que `DocumentItem.embedding` vem `None`.
Impacto: Latência elevada e tempestade de queries HTTP individuais contra o ClickHouse em projetos com histórico real de traces, além de custo quadrático de hashing de embeddings em memória.
Correção sugerida: Implementar leitura em lote de spans/inputs por conjunto de `trace_id`s no `SpanStore` (1–2 queries com `IN`) e pré-computar/cachear `d_emb` uma única vez por documento antes do loop duplo em `identify_knowledge_gaps`.

### HIGH — Worker de webhooks mantém transação aberta e ocupa 2 conexões do pool durante `POST` HTTP externo de 10s
Dimensões: `performance` · Status: **confirmado**
Evidência:
- `server/src/matrix_server/workers/runner.py:137-146`: abre `session = session_factory()` e chama `handler(session, job)`, mantendo essa transação e conexão abertas até o retorno.
- `server/src/matrix_server/mlops/webhooks.py:191-260`: `deliver_webhook_job` lê `WebhookDelivery` e `ModelWebhook` usando `session` e, sem fechá-la/comitar, executa `client.post(webhook.url, ...)` síncrono com `timeout=10.0` (até 4 hops com redirects).
- `server/src/matrix_server/egress/client.py:170-194` e `webhooks.py:177`: `_check`, `_audit` e `_update_delivery_state` abrem uma **segunda** sessão/conexão a partir de `session_factory()` enquanto a primeira sessão do worker permanece ocupada.
Impacto: Cada worker de webhook retém 2 conexões de banco simultaneamente e segura transações abertas durante I/O HTTP externo lento (10s+), arriscando exaustão do pool de conexões ou deadlock em pools pequenos.
Correção sugerida: Carregar `delivery`/`webhook` em transação curta e encerrá-la antes do `client.post(...)` (ou executar a chamada HTTP fora da sessão ativa do worker), gravando a atualização de estado em nova transação curta após o I/O.

### HIGH — Job `server-s3` no CI falha no `scripts/check_skips.py` por skips de rota local não listados em `ALLOWED`
Dimensões: `testes` · Status: **confirmado**
Evidência:
- `.github/workflows/ci.yml:108-111`: job `server-s3` define `MATRIX_TEST_S3_ENDPOINT: http://localhost:9000` e roda `uv run pytest -q -rxXs tests/test_artifacts.py` seguido de `python3 ../scripts/check_skips.py`.
- `server/tests/test_artifacts.py:430-432, 835-838`: `test_above_hash_limit_local_content_route_is_hashed` e `test_quota_rechecked_on_put_content_and_complete` dependem da fixture parametrizada `backend` (`["local", "s3"]`) e invocam `pytest.skip("local content route only")` e `pytest.skip("the content route exists only with the local backend")` no caso `s3`.
- `scripts/check_skips.py:19-25`: `ALLOWED` permite apenas `("tests/test_artifacts.py", "MATRIX_TEST_S3_ENDPOINT not set")`.
Impacto: O job opcional `server-s3` (acionado via label `ci-s3` ou `workflow_dispatch`) falha garantidamente no gate de skips ao executar contra RustFS/S3.
Correção sugerida: Substituir o uso da fixture `backend` parametrizada nesses dois testes por configuração local explícita (`artifact_backend="local"`), eliminando os `pytest.skip` desnecessários.

### HIGH — Mocks e patching de atributo privado em `test_mlops_sdk.py` escondem 2 bugs reais SDK↔Server
Dimensões: `testes` · Status: **confirmado**
Evidência:
- `sdk/python/src/matrix_sdk/mlops.py:67-72`: `_translate_search_space` emite `{"type": "log_uniform", ...}` (e `{"type": "uniform", ...}`), mas `server/src/matrix_server/sweeps/engine.py:71-161` só aceita `choice`/`categorical`, `constant`, `int`, `float` (com `"log": True`) e rejeita `log_uniform`/`uniform` com HTTP 422 (`unsupported parameter type`). `sdk/python/tests/test_mlops_sdk.py:446-459` usa `MockTransport` e não testa `log_uniform`.
- `sdk/python/src/matrix_sdk/tracking.py:258-259, 338, 766-786`: `start_run(..., sweep_id=...)` fora de `matrix_sdk.agent()` inicializa `self._sweep_metric_key = None` e nunca consulta a API do sweep para preenchê-lo; logo `if self._sweep_id and self._sweep_metric_key:` (`line 338`) é sempre falso e pruning nunca é reportado. `sdk/python/tests/test_mlops_sdk.py:285-287` esconde esse bug fazendo patching direto: `run._run._sweep_metric_key = "eval_loss"`.
Impacto: Usuários da camada `MLOpsClient` que criam sweeps `log_uniform`/`uniform` recebem 422 do servidor real, e `start_run(..., sweep_id=...)` com `MatrixTrainerCallback` nunca aciona early stopping.
Correção sugerida: Corrigir `_translate_search_space` (`log_uniform` → `{"type": "float", "log": True}`, `uniform` → `{"type": "float"}`), popular `_sweep_metric_key` em `start_run(..., sweep_id=...)` (via `GET /api/v1/sweeps/{id}` ou campo retornado) e substituir o patching privado/mock parcial por teste de contrato contra a app FastAPI real.

### HIGH — Migração Alembic `0009` sem teste dedicado de `upgrade`/`downgrade` e `t7_sandboxes_benchmarks.py` quebra downgrade com `'sandbox_unavailable'`
Dimensões: `testes` · Status: **confirmado**
Evidência:
- `server/migrations/fragments/t7_sandboxes_benchmarks.py:126-133`: no `downgrade()`, executa `op.alter_column("sandbox_runs", "status", existing_type=sa.String(length=24), type_=sa.String(length=16), ...)` sem truncar ou remapear linhas existentes com `'sandbox_unavailable'` (19 caracteres) antes do `ALTER TYPE` (diferente de `t8_gateway_safety_vectors.py:78-79`).
- `server/tests/test_t7_migration_fragment.py:84` roda apenas em SQLite (onde `if bind.dialect.name != "sqlite":` ignora os `alter_column`), e `.github/workflows/ci.yml:68-69` roda apenas `alembic upgrade head` + `alembic check`, sem testar `downgrade` nem existir teste dedicado para a revisão `20261119_0009_reconciliacao_t5_t8.py` em PostgreSQL.
Impacto: Em PostgreSQL contendo ao menos um `SandboxRun` com status `'sandbox_unavailable'`, `alembic downgrade 0008` aborta com `StringDataRightTruncation`.
Correção sugerida: Remapear/truncar `sandbox_runs.status` > 16 chars (ex.: `'sandbox_unavailable'` → `'failed'`) antes de `alter_column` em `t7_sandboxes_benchmarks.py:downgrade()`, e adicionar teste automatizado em Postgres exercitando `upgrade head` → inserção de `'sandbox_unavailable'` / `NULL`s → `downgrade 0008` → `upgrade head`.

### HIGH — `test_rbac.py` ignora rotas `GET` admin-only de webhooks e omite `sweeps`/`models`/`lineage` de `main_prefixes`; `test_auth_rbac.py` omite `api_key` nas rotas canônicas T5/T6
Dimensões: `testes` · Status: **confirmado**
Evidência:
- `server/tests/test_rbac.py:145-153, 229-265`: `_project_routes` filtra `route.methods & MUTATING`, deixando rotas `GET` com `AdminAuth` (`GET /api/v1/models/{model_id}/webhooks` e `GET /api/v1/mlops/models/{model_id}/webhooks`) fora dos varredores automáticos `test_member_cannot_reach_admin_routes` e `test_key_never_reaches_admin_routes` (que só testam `GET` em `("members", "keys", "audit-log")`). Embora `test_t6_registry_lineage.py:322-323` teste `member`/`viewer`, não há teste de rejeição de `api_key` nesses `GET`s.
- `server/tests/test_rbac.py:207`: `main_prefixes = ("benchmarks", "datasets", "gateway", "mlops", "safety", "sandboxes", "vectors")` omite `"sweeps"`, `"models"` e `"lineage"`.
- `server/tests/test_auth_rbac.py:116-140`: testa rejeição de `api_key` apenas nas rotas legadas `/api/v1/mlops/models/.../stage` e `/api/v1/mlops/models/.../aliases`, não nas rotas canônicas T6 (`/api/v1/models/{id}/versions/{v}/stage` `POST`, `/api/v1/models/{id}/aliases/{alias}` `PUT`/`DELETE`) nem em T5 (`DELETE /api/v1/sweeps/{id}`).
Impacto: Regressões de permissão (especialmente aceitação indevida de `api_key` em rotas administrativas `GET` ou canônicas) passam despercebidas pela suíte de inventário RBAC.
Correção sugerida: Estender `test_rbac.py` para varrer também rotas não-mutantes com `min_role >= ADMIN` (ou incluir `/models/*/webhooks` e `/mlops/models/*/webhooks`), adicionar `"sweeps"`, `"models"`, `"lineage"` em `main_prefixes`, e incluir rotas canônicas T5/T6 em `test_auth_rbac.py`.

### HIGH — Rate limiter em banco do AI Gateway sem testes em PostgreSQL (`pg_insert ... returning`), TPM em DB, `reconcile_tokens` e HTTP real de provedores
Dimensões: `testes` · Status: **confirmado**
Evidência:
- `server/tests/test_ai_gateway.py:35-37, 822-827`: fixa `sqlite:///` em todas as fixtures e no teste multi-instância; o ramo `pg_insert(...).on_conflict_do_update(...).returning(...)` em `server/src/matrix_server/gateway/rate_limiter.py:96-116, 299` nunca é executado nos testes.
- `server/tests/test_ai_gateway.py:258-287, 498-528`: testa TPM apenas em memória (`session_factory=None` via `check_rate_limit`/`record_request`), nunca testando TPM em `_acquire_db` (`rate_limiter.py:126-135`) nem `reconcile_tokens` no banco (`rate_limiter.py:191-212`).
- `server/src/matrix_server/gateway/router.py:302-459`: `OpenAIProvider.complete` e `AnthropicProvider.complete` não têm testes exercitando chamadas HTTP (`httpx.AsyncClient`), tradução de payload Anthropic ou tratamento de erro HTTP real.
Impacto: SQL específico de PostgreSQL (`pg_insert ... returning`), reconciliação real de tokens e tradução/erros HTTP de OpenAI e Anthropic chegam a produção sem cobertura automatizada.
Correção sugerida: Parametrizar `test_ai_gateway.py` com PostgreSQL (`MATRIX_TEST_DATABASE_URL`) cobrindo `acquire`, TPM em DB e `reconcile_tokens`, e adicionar testes para `OpenAIProvider`/`AnthropicProvider` contra servidor HTTP local ou transporte mockado.

---

### MEDIUM — Resumo dos achados verificados (adiados pós-T12)

1. **(`segurança, performance`)** `OpenAIProvider` e `AnthropicProvider` recriam `httpx.AsyncClient(timeout=self.timeout)` a cada request e ignoram `net.py` (`trust_env=False`), herdando proxies de ambiente e perdendo pool de conexões (`server/src/matrix_server/gateway/router.py:335, 419`, `server/src/matrix_server/net.py`) *(fusão Segurança + Performance)*.
2. **(`segurança`)** Ausência de checagem de propriedade por projeto para `sweep_id` em `POST /api/v1/runs` (`server/src/matrix_server/api/runs.py:399-419`) e `dataset_commit_id` em `POST /api/v1/sandboxes/runs` (`server/src/matrix_server/api/sandboxes.py:228-249`).
3. **(`segurança`)** Risco de ReDoS no scorer `regex` de sandboxes (`server/src/matrix_server/sandboxes/runner.py:812-819`) e `TypeError` quando `task_timeout_seconds` ou `max_steps` são `null` explícitos em `sandbox_policy` (`sandboxes/runner.py:188-200, 432, 463, 1049-1050`).
4. **(`segurança`)** Bypass de pré-checagem TPM no AI Gateway com payloads sem espaços: `len(str(content).split()) * 2` estima apenas 2 tokens para strings longas contínuas (`server/src/matrix_server/gateway/router.py:650-654`).
5. **(`falhas-silenciosas`)** Chamadas secundárias em SSR/client engolem erros com `.catch(() => [])` / `null` sem feedback ao usuário: artefatos/detalhe/histórico em `apps/web/src/app/(app)/models/page.tsx:30, 55, 60`, `models-registry-client.tsx:145` e catálogo em `benchmarks/page.tsx:21`.
6. **(`falhas-silenciosas`)** Troca de branch no Dataset Studio mantém itens antigos em erro ou branch vazia (`apps/web/src/components/datasets/dataset-studio-client.tsx:73-81`, `apps/web/src/app/(app)/datasets/[datasetId]/page.tsx:24-36`).
7. **(`falhas-silenciosas`)** Training Studio cacheia `{}` permanentemente em `seriesByRun[runId]` após erro transiente, impedindo novo fetch (`apps/web/src/components/training/training-studio-client.tsx:132, 154-156`).
8. **(`falhas-silenciosas`)** Sandboxes UI engole silenciosamente erros ao buscar transcripts em `handleSelectRun` (`apps/web/src/components/sandboxes/sandboxes-client.tsx:80-82`).
9. **(`falhas-silenciosas`)** Webhook inativo ou deletado deixa `WebhookDelivery` eternamente em `queued` (`server/src/matrix_server/mlops/webhooks.py:218-219`), e `settle_failed_sandbox` sobrescreve `status = "sandbox_unavailable"` para `"failed"` (`server/src/matrix_server/workers/handlers.py:182-184`).
10. **(`falhas-silenciosas`)** `normalizeImportance` lê chave errada contra o formato `[{"param": ..., "importance": ...}]` retornado por `sweeps/engine.py:860` (`apps/web/src/lib/sweeps.ts:60-68`).
11. **(`falhas-silenciosas`)** `apps/web/src/lib/benchmarks.ts` (`176-187, 293-298`) e `apps/web/src/lib/vectors.ts` (`254-315`) fabricam métricas sintéticas de fallback (`"vLLM"/"SGLang"`, `activeGpuRunners: 3`, `sympyEquivAcc`, `"umap"`, `wasserstein: 0.042`, drift de clusters) quando dados reais estão ausentes.
12. **(`falhas-silenciosas`)** Camada de compatibilidade `MLOpsClient` descarta parâmetros (`run_type`, `framework`, `base_model`, `hardware_info`, `git_commit`, `max_concurrent`) e `Run.finish` / `sweeps.py` ignoram erros HTTP 4xx no `PATCH /api/v1/runs/{id}` (`sdk/python/src/matrix_sdk/mlops.py:275-330`, `tracking.py:501-502, 572-582`, `sweeps.py:110-125`).
13. **(`performance`)** `sweep_detail` carrega todos os `Run`s do sweep sem paginação e executa `RandomForestRegressor` sincronamente no `GET` (`server/src/matrix_server/sweeps/service.py:401-468`) além de renderização SVG/DOM não virtualizada na UI de sweeps.
14. **(`performance`)** `list_models` e `get_model` carregam todas as versões e até 100 KB de `model_card` sem paginação (`server/src/matrix_server/api/mlops.py:327, 409, 544-610`), e o SDK faz scans completos em memória.
15. **(`performance`)** Versionamento de datasets carrega todos os `DatasetCommitItem` em memória em `diff_commits` e executa `find_lca` `O(C * (V + E))` (`server/src/matrix_server/datasets/versioning.py:419-535`).
16. **(`performance`)** SSR waterfalls / N+1 em `/training` (até 12 chamadas de reports + 3×2 de métricas sequenciais em `apps/web/src/app/(app)/training/page.tsx:51-116`) e `/datasets/[datasetId]` (`apps/web/src/app/(app)/datasets/[datasetId]/page.tsx:12-36`).
17. **(`performance`)** Layout e destaque de caminho na UI de linhagem fazem passagens `O(V * E)` e BFS com `Array.prototype.shift()` (`apps/web/src/lib/model-lineage.ts:33-44, 98-130`).
18. **(`performance`)** `sdk/python/src/matrix_sdk/sweeps.py:80-86` permite espera ilimitada em `poll_paused`, e `MLOpsClient` chama `_sync_pruning()` duas vezes por `on_log` quando há métricas de treino e eval (`sdk/python/src/matrix_sdk/mlops.py:195-201, 500-502`).
19. **(`testes`)** `scripts/check_skips.py:23` permite o skip de Docker mesmo em CI, e o job `sdk` em `.github/workflows/ci.yml:123` não executa `check_skips.py`.
20. **(`testes`)** `test_live_docker_containment_uid_ro_no_net_no_env_leak` falha em OrbStack/LinuxKit por assumir `/sys/class/net == "lo"` sem considerar interfaces virtuais inativas (`server/tests/test_sandboxes_api.py:613-614`).
21. **(`testes`)** Lacunas de cobertura T6: `max_attempts`, falha de descriptografia e validação de URL em webhooks, cap de itens em `merge`/`revert` de datasets e lacunas em `test_project_delete_main_modules.py`.
22. **(`testes`)** Lacunas de cobertura T5/T8: API de sweeps para `importance` com `>= 10` runs, Hyperband e `poll_paused`; `GET` em scans de safety `not_evaluated`.
23. **(`testes`)** Lacunas de cobertura frontend/E2E: testes Vitest dedicados para `ModelsRegistryClient`/`WebhooksTab` e cenários Playwright específicos para T7/T8.

### LOW — Pendências menores (adiadas)

1. **(`segurança`)** Vulnerabilidades pré-existentes reportadas pelo `npm audit --omit=dev` em `apps/web` (`postcss`/`next`, `echarts`).
2. **(`falhas-silenciosas`)** `RegisterModelModal` engole erro ao listar modelos existentes em `apps/web/src/components/training/register-model-modal.tsx:87-89`.
3. **(`performance`)** `SafetyDashboard` inclui `findings` nas dependências do `useEffect` causando reexecuções extras (`apps/web/src/components/safety/safety-dashboard.tsx:58-102`), `_fetch_hop_edges` aplica `limit(row_limit)` por subquery podendo exceder o teto agregado antes do corte (`mlops/lineage.py:179-425`), e `BenchmarksClient` carece de `useMemo` em derivações pesadas.
4. **(`testes`)** Lacunas menores de teste e falha local no SDK macOS por ausência de `libomp.dylib`.

---

## Backlog

### Execução imediata (T12 — CRITICAL & HIGH confirmados)
- `fix · small` — Criar `BenchmarkRun` ao enfileirar `mlops.evaluate` e falhar payload sem `benchmark_run_id` no worker em `api/mlops.py:1339-1373` e `workers/handlers.py:128-137` (CRITICAL, confirmado)
- `fix · small` — Restringir `runner_config.api_key_env` a allowlist/prefixo dedicado e validar egress antes de ler segredo sem oráculo em `benchmarks/runner.py:26, 94-99, 290-296, 494` (CRITICAL, confirmado)
- `fix · small` — Remover fallback silencioso para `MOCK_GATEWAY_*` e preservar `NEXT_REDIRECT` com `unstable_rethrow` em `apps/web/src/app/(app)/gateway/page.tsx:20-48` (CRITICAL, confirmado)
- `fix · standard` — Substituir full-table scan e ordenação em Python por agregação SQL em `get_stats` e `get_providers_status` (`gateway/router.py:954-1031`) (CRITICAL, confirmado)
- `fix · standard` — Tirar I/O síncrono de DB do event loop `async` e converter loop de até 500 `DELETE`s em delete em lote no AI Gateway (`api/gateway.py:130-150`, `gateway/router.py`, `gateway/rate_limiter.py:241-263`) (CRITICAL, confirmado)
- `fix · trivial` — Ler `(sw.config or {}).get("method")` em `mlops/lineage.py:572` e parar de mascarar erro de linhagem como grafo vazio na UI de modelos (HIGH, confirmado)
- `fix · trivial` — Remover parametrização `s3` dos testes exclusivos de rota local em `server/tests/test_artifacts.py:430-432, 835-838` para destravar `server-s3` no `check_skips.py` (HIGH, confirmado)
- `fix · small` — Proteger `sync_exec=True` em `POST /api/v1/benchmarks/runs` com `is_test_env()` e limitar duração/concorrência (`api/benchmarks.py:164-168, 250-264`, `benchmarks/runner.py:287, 305-340`) (HIGH, confirmado)
- `fix · small` — Impedir HTTP 500 síncrono e retries/status `done` inconsistente ao propagar `EngineUnavailable`/`EgressError` em `benchmarks/runner.py:485-496` e `workers/runner.py:157` (HIGH, confirmado)
- `fix · small` — Falhar fechado para scorer desconhecido (`422`/`0.0`), exigir alvo real em `llm_judge` e propagar/registrar falhas de `ArtifactStore` sem `verified=True` em `sandboxes/runner.py:854-864, 885-897, 933-939` (HIGH, confirmado)
- `fix · small` — Aplicar `unstable_rethrow` em todos os `try/catch` de Server Components `(app)` que invocam `apiGet` (`models`, `training`, `sweeps`, `sweeps/[sweepId]`, `lineage`, `benchmarks`, `sandboxes`, `safety`, `datasets/[datasetId]`) (HIGH, confirmado)
- `fix · small` — Impedir fabricação de `"Compliant"` (100%) / `"Low Risk"` em `apps/web/src/app/(app)/safety/page.tsx:24-31` e `apps/web/src/lib/safety.ts:196-205, 290-303, 440-481` (HIGH, confirmado)
- `fix · small` — Truncar/remapear `'sandbox_unavailable'` antes de `alter_column` no `downgrade()` de `t7_sandboxes_benchmarks.py:126-133` e adicionar teste Postgres de `upgrade`/`downgrade` para a revisão `0009` (HIGH, confirmado)
- `fix · small` — Corrigir `_translate_search_space` (`log_uniform`/`uniform` → `float`) em `sdk/python/src/matrix_sdk/mlops.py:67-72`, popular `_sweep_metric_key` em `start_run(sweep_id=...)` (`tracking.py:766-786`) e eliminar patching privado/mocks parciais em `test_mlops_sdk.py` (HIGH, confirmado)
- `fix · small` — Incluir rotas `GET` admin-only de webhooks em `test_rbac.py`, atualizar `main_prefixes` com `sweeps`/`models`/`lineage` e testar rejeição de `api_key` nas rotas canônicas T5/T6 em `test_auth_rbac.py` (HIGH, confirmado)
- `fix · standard` — Remover cálculo sintético via SHA-256 com `simulated=False` em `HttpBenchmarkEngine` (`benchmarks/runner.py:160-161, 347-372`), marcando `simulated=True` ou levantando `EngineUnavailable` até existir avaliador real (HIGH, confirmado)
- `fix · standard` — Isolar `CircuitBreaker` por `(project_id, provider)` (sem contar 4xx do cliente) e remover vazamento de estado global em `gateway/router.py` e `api/gateway.py` (HIGH, confirmado)
- `fix · standard` — Adicionar testes PostgreSQL (`pg_insert ... returning`), TPM em DB, `reconcile_tokens` e HTTP mockado para `OpenAIProvider`/`AnthropicProvider` em `server/tests/test_ai_gateway.py` (HIGH, confirmado)
- `fix · standard` — Liberar sessão/transação do worker de webhooks antes do `POST` HTTP externo de 10s em `mlops/webhooks.py:191-260` e `workers/runner.py:137-146` (HIGH, confirmado)
- `fix · standard` — Eliminar N+1 de `SpanStore.get_trace` na Vectors API (`api/vectors.py:68-76, 245-268`) com busca em lote e pré-computar embeddings de documentos em `vectors/projection.py:355-370` (HIGH, confirmado)
- `fix · standard` — Adquirir `for_update` em `Run`/`Sweep` em `report_metric`, filtrar por rung antes de consultar peers e evitar leitura de 5000 pontos por step (`sweeps/service.py:319-391`, `api/sweeps.py:180-183`) (HIGH, confirmado)
- `fix · standard` — Reduzir escopo do lock `FOR UPDATE` em `next_trial` (`sweeps/service.py:154-280`), usar `bisect` `O(n log n)` no TPE e permutação `O(1)` no grid com seed (`sweeps/engine.py:426-479`) (HIGH, confirmado)

### Pendências adiadas (MEDIUM & LOW)
- `fix · small` — Reusar `httpx.AsyncClient` com `trust_env=False` / helpers de `net.py` em `OpenAIProvider` e `AnthropicProvider` (`gateway/router.py:335, 419`) (MEDIUM, adiado)
- `fix · trivial` — Validar propriedade por projeto de `sweep_id` em `api/runs.py:399-419` e `dataset_commit_id` em `api/sandboxes.py:228-249` (MEDIUM, adiado)
- `fix · small` — Validar/proteger regex contra ReDoS e rejeitar `null` explícito para `task_timeout_seconds`/`max_steps` em `sandboxes/runner.py` (MEDIUM, adiado)
- `fix · small` — Substituir estimativa de tokens via `.split()` por estimativa por bytes/caracteres (ex.: `ceil(len / 4)`) em `gateway/router.py:650-654` (MEDIUM, adiado)
- `fix · small` — Expor erros em chamadas secundárias de `models/page.tsx`, `models-registry-client.tsx`, `benchmarks/page.tsx`, `dataset-studio-client.tsx`, `datasets/[datasetId]/page.tsx`, `training-studio-client.tsx` e `sandboxes-client.tsx` (MEDIUM, adiado)
- `fix · small` — Atualizar `WebhookDelivery.status` quando webhook está inativo/ausente (`webhooks.py:218-219`) e preservar `sandbox_unavailable` em `settle_failed_sandbox` (`handlers.py:182`) (MEDIUM, adiado)
- `fix · trivial` — Corrigir chave lida por `normalizeImportance` em `apps/web/src/lib/sweeps.ts:60-68` para o formato de `sweeps/engine.py:860` (MEDIUM, adiado)
- `fix · small` — Remover fallbacks sintéticos de `apps/web/src/lib/benchmarks.ts` e `apps/web/src/lib/vectors.ts` quando dados estiverem vazios/ausentes (MEDIUM, adiado)
- `fix · small` — Repassar ou deprecar parâmetros ignorados de `MLOpsClient` e verificar status HTTP em `Run.finish` / `sweeps.py` (`sdk/python/src/matrix_sdk/`) (MEDIUM, adiado)
- `refactor · standard` — Paginar `sweep_detail` / `list_models` / `diff_commits` / `find_lca` e mover `RandomForestRegressor` de `GET /sweeps/{id}` para cálculo assíncrono ou cache pré-computado (MEDIUM, adiado)
- `fix · small` — Otimizar `training/page.tsx`, `datasets/[datasetId]/page.tsx`, `apps/web/src/lib/model-lineage.ts` e `sdk/python/src/matrix_sdk/sweeps.py` (`poll_paused`, `_sync_pruning`) (MEDIUM, adiado)
- `fix · small` — Exigir Docker em CI no `check_skips.py`, adicionar `check_skips.py` no job `sdk`, ajustar checagem `/sys/class/net` para OrbStack/LinuxKit e cobrir lacunas de testes T5–T8 e Playwright/Vitest (MEDIUM, adiado)
- `fix · trivial` — Atualizar dependências afetadas por `npm audit --omit=dev` em `apps/web`, tratar erro em `RegisterModelModal`, ajustar deps/memo/limit em `SafetyDashboard`, `BenchmarksClient` e `_fetch_hop_edges`, e documentar/resolver `libomp.dylib` no macOS (LOW, adiado)
