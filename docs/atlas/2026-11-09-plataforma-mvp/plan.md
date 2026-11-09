# MATRIX — Plano do MVP LLMOps

Spec: [spec.md](spec.md) · Ledger: [ledger.md](ledger.md) · ADRs: [docs/adr](../../adr)

Cada tarefa leva tag de lane e nível de complexidade (`atlas-dispatch`). Modelo e esforço saem da tabela do ATLAS: C1 haiku/low · C2 sonnet/low · C3 sonnet/medium · C4 opus/medium · C5 opus/high (teto). Revisor: piso C2; `security-reviewer`: piso C4.

## Tabela de custo (Gate 1)

| Tarefa | Fatia | Tag | Nível | Implementa | Revisa |
|---|---|---|---|---|---|
| T01 ADRs 001–005 | F0 | `[arch]` | C4 | orquestrador (inline) | — |
| T02 Scaffold do monorepo, Makefile, compose, CI | F0 | `[core]` | C2 | sonnet/low | sonnet/low |
| T03 Ingestão OTLP → modelo OpenInference → SpanStore (memória + ClickHouse) | F1 | `[arch]` | C5 | opus/high | opus/high |
| T04 Projetos e chaves de API (hash, isolamento) | F1 | `[core]` | C4 | opus/medium | opus/medium + segurança opus/medium |
| T05 Motor de custo (tabela de preços) | F1 | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T06 API de consulta: traces, sessões, stats, scores | F1 | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T07 SDK Python (exportador, prompts, scores) | F1 | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T08 Inspector web: lista, waterfall, grafo, payload | F2 | `[design]` | C4 | opus/medium | opus/medium |
| T09 Modelos Postgres + fila de jobs (SKIP LOCKED) | F4 | `[arch]` | C4 | opus/medium | opus/medium |
| T10 Biblioteca de métricas (RAG, G-Eval, asserções) + providers | F4 | `[core]` | C4 | opus/medium | opus/medium |
| T11 Runner de experimentos + worker + comparação com baseline | F4 | `[core]` | C4 | opus/medium | opus/medium |
| T12 Web: datasets, span → item, matriz com diff | F4 | `[design]` | C4 | opus/medium | opus/medium |
| T13 Fila de anotação, regras, export DPO/regressão | F5 | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T14 Web: fila de anotação | F5 | `[design]` | C3 | sonnet/medium | sonnet/medium |
| T15 Prompt CMS (API) + cache no SDK | F3 | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T16 Web: prompts e labels | F3 | `[design]` | C3 | sonnet/medium | sonnet/medium |
| T17 Avaliação online por amostragem + alertas | F6 | `[core]` | C4 | opus/medium | opus/medium |
| T18 Demo ponta a ponta + verificação do critério de pronto | — | `[core]` | C3 | sonnet/medium | — |

Gatilhos de segurança: T03 (entrada externa, payload com PII), T04 (credenciais), T10/T11/T17 (chamadas a APIs externas). `security-reviewer` roda nessas.

## Ordem (janela autônoma de 8h)

F0 → F1 → F2 ∥ (T09–T11) → T12 → F5 → F3 → F6 → T18. Backend e web andam em paralelo quando tocam diretórios diferentes; tarefas de backend que mexem no mesmo pacote rodam em sequência.

## Tarefas

- [ ] **T01** `[arch] · C4` — ADRs: protocolo OTLP+OpenInference; armazenamento poliglota; fila de jobs; stack (FastAPI + Next.js); multi-tenant por chave de projeto.
- [ ] **T02** `[core] · C2` — `server/` (uv, FastAPI, pytest, ruff), `sdk/python/`, `apps/web/` (Next.js + vitest), `deploy/compose.yaml`, `Makefile` (`make test`, `make dev`), `.github/workflows/ci.yml`.
- [ ] **T03** `[arch] · C5` — decodificar OTLP protobuf/JSON; mapear atributos OpenInference (`openinference.span.kind`, `llm.*`, `input.*`, `output.*`, `retrieval.documents.*`, `tool.*`, `session.id`, `user.id`, mensagens e imagens); truncamento; `SpanStore` com `MemorySpanStore` e `ClickHouseSpanStore` (schema e migração); testes com fixtures OTLP reais.
- [ ] **T04** `[core] · C4` — tabela de projetos e chaves (hash SHA-256, prefixo, `hmac.compare_digest`); dependência FastAPI; endpoints admin; CLI `matrix-admin`; teste de isolamento entre projetos.
- [ ] **T05** `[core] · C3` — `prices.json` (input, output, cache read/write por 1M tokens) para modelos comuns; custo por span e por trace; override por variável de ambiente.
- [ ] **T06** `[core] · C3` — endpoints de traces, sessões, stats e scores do contrato.
- [ ] **T07** `[core] · C3` — `matrix_sdk.init(...)`, `score(...)`, `get_prompt(...)` (cache), exemplo instrumentado.
- [ ] **T08** `[design] · C4` — páginas de traces, detalhe (waterfall com TTFT, grafo de agentes, painel do span: mensagens, documentos, ferramenta, mídia, atributos), sessões, visão geral.
- [ ] **T09** `[arch] · C4` — SQLAlchemy: prompts, datasets, itens, experimentos, resultados, scores, anotações, regras, avaliações online, alertas, jobs; fila `jobs` com `FOR UPDATE SKIP LOCKED` (no SQLite, lock otimista).
- [ ] **T10** `[core] · C4` — métricas determinísticas e com juiz (faithfulness por proposições, answer relevancy por perguntas reversas + embeddings, context precision, G-Eval); providers `openai`, `anthropic`, `fake`.
- [ ] **T11** `[core] · C4` — `POST /experiments` enfileira; worker executa variantes × itens, pontua, resume, compara com baseline.
- [ ] **T12** `[design] · C4` — páginas de datasets, botão "adicionar ao dataset" no span, criação de experimento e matriz com diff e regressões.
- [ ] **T13** `[core] · C3` — fila de anotação, regras avaliadas na ingestão e na chegada de scores, export DPO e regressão.
- [ ] **T14** `[design] · C3` — página da fila: trace, notas, correção, salvar como regressão/DPO.
- [ ] **T15** `[core] · C3` — API de prompts com versões imutáveis e labels; SDK com cache TTL e refresh em segundo plano.
- [ ] **T16** `[design] · C3` — páginas de prompts, versões, diff e mover label.
- [ ] **T17** `[core] · C4` — avaliações online: amostragem determinística por trace, job de score, regras de alerta.
- [ ] **T18** `[core] · C3` — `examples/rag_demo.py` gera tráfego; roteiro que verifica os 6 critérios de pronto.

## Estado para retomada

Atualizado a cada fatia no ledger (seção "Progresso").
