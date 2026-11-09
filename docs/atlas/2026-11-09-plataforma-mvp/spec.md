# MATRIX — Spec do MVP LLMOps

```
ATLAS · mvp · arquitetura+dados+infra+design+produto+auditoria · large → pesquisa, descoberta, plano + ADR, G1, SDD (despacho via atlas-dispatch), revisão + segurança, verificação, G2 · lanes: design, arquitetura, auditoria
```

Origem: [docs/blueprint-sota.md](../../blueprint-sota.md) (mapeamento SOTA de MLOps/LLMOps/AgentOps e blueprint de 5 camadas).
Paradigmas `dados` e `infra` não têm lane no ATLAS: caem no núcleo, com `clickhouse-io`, `postgres-patterns` e `eval-harness` do ECC como referência (candidatos a um módulo `stack-data` no ATLAS).

## 1. Problema

Times que constroem RAG e agentes usam 4 a 6 ferramentas separadas (tracing, prompts, avaliação, anotação, drift, registro de modelos). Os dados ficam em silos e o ciclo "falha em produção → caso de teste → correção → regressão" é manual. O MATRIX unifica isso numa plataforma só, começando pelo LLMOps.

## 2. Escopo do MVP (fatia LLMOps)

Dentro:

| Módulo do blueprint | O que entra no MVP |
|---|---|
| Camada 2 — ingestão | receptor OTLP/HTTP (`/v1/traces`, protobuf e JSON) com semântica OpenInference |
| Camada 3 — armazenamento | ClickHouse (spans), PostgreSQL (projetos, chaves, prompts, datasets, experimentos, scores, fila, regras) |
| Módulo 3 — Trace & Agent Inspector | sessões → traces → spans; waterfall com TTFT; grafo de agentes; payloads, mensagens, documentos recuperados e imagens inline |
| Módulo 1 — Prompt CMS | versões imutáveis, labels `development`/`staging`/`production`, cache no SDK |
| Módulo 4 — Eval Matrix | golden datasets; span → item em 1 clique; matriz variantes (prompt × modelo) × casos; métricas RAG (faithfulness, answer relevancy, context precision), G-Eval e asserções determinísticas; comparação com baseline |
| Módulo 6 — flywheel | fila de anotação alimentada por regras; correção humana vira caso de regressão e par DPO (JSONL) |
| Avaliação online | juízes em amostragem configurável do tráfego |

Fora (roadmap, cada um é um novo ciclo ATLAS): Vector & Drift (UMAP 3D, Evidently), MLOps (tracking de treino, Model/LoRA Registry, LakeFS), red-teaming OWASP e sandboxes Inspect, benchmarks em GPU (lm-eval-harness, VLMEvalKit), AI Gateway, login de usuários na UI e RBAC.

## 3. Arquitetura

```
apps/web (Next.js)  ──server-side──►  server (FastAPI)  ──►  ClickHouse (spans)
                                         │   ▲                PostgreSQL (metadados, fila de jobs)
SDK Python / qualquer exportador OTLP ───┘   └── worker (mesmo pacote, outro processo)
```

- `server/` — pacote Python `matrix_server` (FastAPI, SQLAlchemy 2, pydantic v2). Processos: `matrix-api` e `matrix-worker`.
- `sdk/python/` — pacote `matrix-sdk`: configura o exportador OTLP com a chave do projeto, busca prompts com cache, registra scores.
- `apps/web/` — Next.js (App Router, TypeScript). Fala com a API só no servidor (a chave nunca vai ao navegador).
- `deploy/compose.yaml` — ClickHouse, PostgreSQL (pgvector), MinIO, API, worker, web.
- Modo dev sem infraestrutura: `MATRIX_SPAN_STORE=memory` e `MATRIX_DATABASE_URL=sqlite:///...`.

Decisões em `docs/adr/`.

## 4. Contrato da API (fonte para web e SDK)

Autenticação: `Authorization: Bearer mx_<chave>` (ou `x-matrix-api-key`). A chave identifica o projeto; toda leitura e escrita é filtrada por ele. Administração (`/api/v1/admin/*`) usa `MATRIX_ADMIN_TOKEN`. Datas em ISO 8601 UTC. Paginação por `limit` (padrão 50, máx 500) e `cursor` opaco.

### Ingestão
- `POST /v1/traces` — OTLP/HTTP, `application/x-protobuf` ou `application/json`. Resposta OTLP (`{}` / `ExportTraceServiceResponse`). Limite de corpo configurável (padrão 10 MB).

### Traces, sessões, scores
- `GET /api/v1/traces?session_id&user_id&name&status&kind&since&until&limit&cursor` → `{items: TraceSummary[], next_cursor}`
- `GET /api/v1/traces/{trace_id}` → `{trace: TraceSummary, spans: Span[]}`
- `GET /api/v1/sessions?limit&cursor` → `{items: SessionSummary[], next_cursor}`
- `GET /api/v1/sessions/{session_id}` → `{session: SessionSummary, traces: TraceSummary[]}`
- `GET /api/v1/stats/overview?since` → `{trace_count, error_rate, p50_latency_ms, p95_latency_ms, total_cost_usd, input_tokens, output_tokens, by_model: [{model, calls, cost_usd}]}`
- `POST /api/v1/scores` `{trace_id, span_id?, name, value, label?, comment?, source: "human"|"llm"|"rule"|"sdk"}` → `Score`
- `GET /api/v1/traces/{trace_id}/scores` → `Score[]`

```ts
type SpanKind = "LLM"|"RETRIEVER"|"RERANKER"|"TOOL"|"AGENT"|"GUARDRAIL"|"CHAIN"|"EMBEDDING"|"EVALUATOR"|"UNKNOWN";
type TraceSummary = { trace_id: string; name: string; session_id: string|null; user_id: string|null;
  start_time: string; end_time: string; duration_ms: number; status: "ok"|"error"; span_count: number;
  input_tokens: number; output_tokens: number; total_cost_usd: number; root_kind: SpanKind;
  input_preview: string|null; output_preview: string|null; tags: string[]; scores: Record<string, number> };
type Message = { role: string; content: string; tool_calls?: {name: string; arguments: string}[] };
type Document = { id: string|null; content: string; score: number|null; metadata: Record<string, unknown> };
type Media = { kind: "image"|"audio"|"file"; url: string; mime: string|null };
type Span = { span_id: string; trace_id: string; parent_span_id: string|null; name: string; kind: SpanKind;
  start_time: string; end_time: string; duration_ms: number; status: "ok"|"error"|"unset"; status_message: string|null;
  model: string|null; provider: string|null; input_tokens: number; output_tokens: number; reasoning_tokens: number;
  cost_usd: number; ttft_ms: number|null; input: string|null; output: string|null;
  input_messages: Message[]; output_messages: Message[]; documents: Document[];
  tool: {name: string; description: string|null; parameters: string|null}|null; media: Media[];
  invocation_parameters: Record<string, unknown>; attributes: Record<string, unknown>;
  events: {name: string; time: string; attributes: Record<string, unknown>}[] };
type SessionSummary = { session_id: string; user_id: string|null; trace_count: number; first_seen: string;
  last_seen: string; total_cost_usd: number };
type Score = { id: string; trace_id: string; span_id: string|null; name: string; value: number; label: string|null;
  comment: string|null; source: string; created_at: string };
```

### Prompts
- `GET /api/v1/prompts` → `{items: [{name, latest_version, labels: Record<label, version>, updated_at}]}`
- `POST /api/v1/prompts` `{name, type: "text"|"chat", template: string | Message[], config?: {model?, temperature?, max_tokens?, ...}, response_schema?, labels?: string[], commit_message?}` → `PromptVersion` (nova versão; versões são imutáveis)
- `GET /api/v1/prompts/{name}?label=production|version=N` → `PromptVersion` (sem parâmetro: label `production`, senão a última)
- `GET /api/v1/prompts/{name}/versions` → `{items: PromptVersion[]}`
- `POST /api/v1/prompts/{name}/labels` `{label, version}` → move o label (rollback = mover de volta)
- `PromptVersion = {name, version, type, template, variables: string[], config, response_schema, labels: string[], commit_message, created_at}`; variáveis no formato `{{nome}}`.

### Datasets e experimentos
- `POST /api/v1/datasets` `{name, description?}`; `GET /api/v1/datasets` → `{items: [{id, name, description, item_count, created_at}]}`
- `GET /api/v1/datasets/{id}` → `{dataset, items: DatasetItem[]}`
- `POST /api/v1/datasets/{id}/items` `{input, expected_output?, context?: string[], metadata?}`; `POST /api/v1/datasets/{id}/items/from-span` `{trace_id, span_id?}`; `DELETE /api/v1/datasets/{id}/items/{item_id}`
- `GET /api/v1/datasets/{id}/export.jsonl` — casos de regressão
- `DatasetItem = {id, dataset_id, input: Record<string, unknown>, expected_output: string|null, context: string[], metadata, source_trace_id, source_span_id, created_at}`
- `POST /api/v1/experiments` `{name, dataset_id, baseline_experiment_id?, variants: [{label, prompt: {name, version}|{template, type}, model: {provider, name, params?}}], metrics: [{type, name?, params?, threshold?}]}` → `{id, status: "queued"}`
- `GET /api/v1/experiments` → `{items: [{id, name, dataset_id, status, created_at, finished_at, summary}]}`
- `GET /api/v1/experiments/{id}` → `{experiment, results: ExperimentResult[], summary: Record<variant, Record<metric, {mean, pass_rate}>>, regressions: {item_id, variant, metric, baseline, current}[]}`
- `ExperimentResult = {item_id, variant, output, latency_ms, cost_usd, input_tokens, output_tokens, error, scores: Record<metric, {value, passed, reason}>}`
- Métricas: `faithfulness`, `answer_relevancy`, `context_precision`, `g_eval` (param `criteria`), `equals`, `contains`, `regex`, `is_json` (param `schema` opcional), `levenshtein`, `rouge_l`, `cost` (param `max_usd`), `latency` (param `max_ms`).
- Providers de modelo: `openai` (compatível: OpenAI, vLLM, Ollama via `base_url`), `anthropic`, `fake` (determinístico, para testes e demo).

### Anotação, regras e avaliação online
- `GET /api/v1/annotations?status=pending|done` → `{items: [{id, trace_id, reason, status, created_at, completed_at}]}`
- `POST /api/v1/annotations` `{trace_id, reason}`
- `POST /api/v1/annotations/{id}/complete` `{scores?: [{name, value}], corrected_output?, add_to_dataset_id?, comment?}` — com `corrected_output`, grava o par DPO `{prompt, chosen: corrected_output, rejected: output original}`; com `add_to_dataset_id`, vira caso de regressão.
- `GET /api/v1/exports/dpo.jsonl`
- `POST /api/v1/rules` `{name, enabled, condition: {score_name?, op: "<"|"<="|">"|">="|"==", value?, feedback?: "negative", min_latency_ms?, status?: "error"}, actions: ["annotate"|"add_to_dataset"|"alert"], dataset_id?}`; `GET /api/v1/rules`; `DELETE /api/v1/rules/{id}`
- `POST /api/v1/online-evals` `{name, metric, params?, sampling_rate (0..1), filter?: {kind?, name?}, model?: {provider, name}}`; `GET /api/v1/online-evals`; `DELETE /api/v1/online-evals/{id}`
- `GET /api/v1/alerts` → `{items: [{id, rule_id, trace_id, message, created_at}]}`

### Administração
- `POST /api/v1/admin/projects` `{name}` → `{project: {id, name}, api_key}` (a chave só aparece aqui)
- `POST /api/v1/admin/projects/{id}/keys` → `{api_key}`; CLI equivalente: `matrix-admin create-project <nome>`.

## 5. Segurança (gatilhos do ATLAS)

- Chaves de API: guardadas só como hash SHA-256 com prefixo visível; comparação em tempo constante.
- Isolamento por projeto em toda consulta (ClickHouse e Postgres); teste que tenta ler trace de outro projeto.
- Payloads de traces podem ter PII: nada de logar corpo; limite de tamanho por atributo (truncamento) e de corpo da requisição.
- Chamadas a LLM juiz e providers: chaves só por variável de ambiente, nunca persistidas.
- UI sem login no MVP (implantação de time único atrás de rede privada): registrado como risco aceito no ledger e primeiro item do próximo ciclo.

## 6. Critério de pronto do MVP

1. Um app instrumentado (SDK MATRIX + instrumentor OpenInference) envia traces e eles aparecem no inspector com waterfall, grafo e custo.
2. Um span vira item de golden dataset em 1 clique.
3. A matriz roda 2 variantes de prompt × 2 modelos sobre o dataset com faithfulness, answer relevancy e asserções, mostrando regressões contra a baseline.
4. Uma regra manda traces ruins para a fila; a correção humana vira caso de regressão e par DPO exportado.
5. Avaliação online pontua uma amostra do tráfego e dispara alerta.
6. `make test` verde (server, sdk, web) e `docker compose up` sobe a stack.
