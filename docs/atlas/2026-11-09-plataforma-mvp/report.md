# Relatório — MVP LLMOps do MATRIX

Branch `ccr-9443eae6-fk5867` · 2026-11-09 · Gate 2 pendente (PR/merge é decisão do usuário)

```
ATLAS · mvp · arquitetura+dados+infra+design+produto+auditoria · large → G1 (plano aprovado), SDD com despacho por complexidade, 2 revisões de segurança, verificação, G2 pendente
```

## Resultado

As 6 fatias do MVP (F0–F6) e a demo ponta a ponta (T18) estão no branch. Os 6 critérios de pronto da spec passam contra ClickHouse 24.8 e PostgreSQL 16 reais:

```
$ make demo
PASS  1. Traces com spans, custo e árvore de agente
PASS  2. Span vira item de dataset
PASS  3. Matriz 2 prompts x 2 modelos com regressões
PASS  4. Regras -> fila -> correção -> DPO + dataset
PASS  5. Avaliação online + alerta
PASS  6. make test
```

| Suíte | Resultado |
|---|---|
| server (pytest, SQLite + PostgreSQL + ClickHouse reais) | 479 passed |
| server sem bancos externos (`make test`) | 348 passed, 131 skipped |
| sdk/python | 19 passed |
| apps/web (vitest) | 97 passed; `tsc` e `next build` limpos |
| CI GitHub Actions (com serviços Postgres e ClickHouse + `alembic check`) | verde |

## O que foi entregue

| Fatia | Entrega |
|---|---|
| F0 | monorepo `server/` (FastAPI), `sdk/python/`, `apps/web/` (Next.js), `deploy/compose.yaml`, Makefile, CI, ADRs 001–005 |
| F1 | `POST /v1/traces` OTLP (protobuf/JSON, gzip) → OpenInference/GenAI → ClickHouse (`ReplacingMergeTree`, dedup de retry) ou memória; projetos e chaves com hash; custo por tabela de preços; API de traces, sessões, stats e scores; SDK com helpers, sessões, prompts com cache |
| F2 | inspector: visão geral, traces, waterfall com TTFT, grafo de agentes com loops agregados, painel do span (mensagens, documentos, ferramenta, mídia, atributos), sessões |
| F3 | Prompt CMS: versões imutáveis, labels/rollback, diff, playground; SDK com stale-while-revalidate |
| F4 | datasets (span → item, export JSONL), experimentos variantes × itens no worker, métricas faithfulness / answer relevancy / context precision / G-Eval + 8 asserções, providers openai/anthropic/fake, Eval Matrix com regressões e decomposição por proposição |
| F5 | regras (score, feedback, latência, erro) → fila de anotação / dataset / alerta; revisão humana gera par DPO e caso de regressão |
| F6 | avaliação online por amostragem determinística, scores do juiz disparam regras e alertas |

Capturas em [screenshots/](screenshots/) (`real-*.png` são com dados reais da API).

## Roteamento por complexidade (atlas-dispatch)

Todo subagente recebeu modelo e esforço pela complexidade da tarefa; nenhum passou de `high`.

| Tarefa | Nível | Modelo/esforço |
|---|---|---|
| T02 scaffold | C2 | sonnet/low |
| T03 ingestão OTLP + store | C5 | opus/high |
| Revisão F1 (código + segurança) e correção | C5 | opus/high |
| T04 auth, T08 inspector, T09 modelos/fila, T10 métricas, T11 runner, T12 Eval Matrix, T17 avaliação online | C4 | opus/medium |
| Revisão de segurança T09–T17 e correção | C4 | opus/medium |
| T05 custo, T06 consulta, T07 SDK, T13 regras, T14+T16 web, T15 prompts, T18 demo, integração web × API | C3 | sonnet/medium |

Nenhuma tarefa precisou de escalonamento (nenhuma falhou duas vezes). Detalhes no [ledger](ledger.md).

## Revisões de segurança

- **F1 (opus/high):** 2 HIGH corrigidos: amplificação de memória por atributos de resource e duplicatas de retry inflando agregados. Também foram corrigidos 4 MEDIUM e 8 LOW.
- **Backend de avaliação (opus/medium):** 4 HIGH corrigidos: lease sem renovação, ReDoS na métrica regex, hooks de regra ausentes no worker e falta de limite de corpo. Também foram corrigidos 2 MEDIUM.
- **Confirmado correto:** isolamento por projeto em todas as consultas e jobs, SQL parametrizado, chaves só como hash, sem SSRF (base URL só por ambiente), erros de provider sem segredo.

## Riscos aceitos e pendências

1. **UI sem login** (ruling do ledger): a chave fica só no servidor do Next.js. Não exponha a web fora de rede privada; login e RBAC são o primeiro item do próximo ciclo.
2. **`loading.tsx` removidos:** com latência real, eles impediam a navegação na mesma rota no Next 15.5.29. Os skeletons de carregamento sumiram até investigar a causa ou atualizar o Next.
3. **`docker compose` não foi executado** (sem daemon Docker no ambiente). Os arquivos foram revisados e as migrações agora rodam antes da API.
4. **Manifesto do ATLAS com fontes `local`:** depois do merge do branch do ATLAS, rode `npx github:Andre-Atlas/ATLAS update` aqui para fixar os commits.
5. **Branch padrão:** o repositório estava vazio, então `ccr-9443eae6-fk5867` virou o padrão. Crie `main` no Gate 2.
6. **Detalhes menores:** o formulário de novo experimento sugere um prompt `rag-answer` fictício; não existe `GET /annotations/{id}` (a UI pagina a fila); o juiz `fake` serve só para testes e demo, e números reais pedem `MATRIX_JUDGE_PROVIDER=openai|anthropic`.

## Próximo ciclo sugerido (roadmap do blueprint)

1. Login + RBAC na UI.
2. Vector & Drift (UMAP 3D, Evidently).
3. MLOps (tracking de treino, Model/LoRA Registry, LakeFS).
4. Red-teaming OWASP e sandboxes Inspect.
5. Benchmarks em GPU.
6. AI Gateway.
