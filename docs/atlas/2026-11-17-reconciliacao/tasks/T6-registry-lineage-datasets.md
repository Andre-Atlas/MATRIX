# T6 — Registry de modelos/LoRA, linhagem e datasets (branch/merge + golden)

`[core]` · nível **C4**, gatilho de segurança (webhooks → SSRF, promoção) · ATLAS: opus/medium + revisão de segurança (Antigravity: `pro`) · Depende de: T4 (concluída). Pode rodar em paralelo com T5, T7, T8 se os módulos não colidirem (T6 mexe em `mlops/` registry, `api/mlops.py`, `datasets/`, `api/datasets.py`).
Leia primeiro: [HANDOFF.md](../HANDOFF.md), `docs/atlas/2026-11-10-plataforma-completa/contracts/g3.md`, `docs/adr/013-reconciliacao-main-branch.md`, `docs/adr/014-*` e `015-*` (ADRs vindos do main), `analise.md` (área B: registry, datasets, linhagem), `t4-notes.md`, `docs/adr/012-politica-de-saida.md`.

## Objetivo
Evoluir o registry, a linhagem e o versionamento de datasets que já existem no `main` (agora sobre `runs`/`artifacts`), fechando as lacunas do contrato g3 sem reescrever o que funciona.

## Fazer
1. **Registry (código do main em `mlops/` + `api/mlops.py`):**
   - `production` **único por modelo**: promover uma versão rebaixa a anterior a `archived` na mesma transação; registrar em tabela nova `model_stage_history(model_id, version, from, to, actor_kind, actor_id, comment, ts)` e expor `GET .../history`.
   - Aliases: `champion` exige ADMIN (já existe), validar nomes `^[a-z][a-z0-9_-]{0,49}$`; promover a `production` e arquivar exigem ADMIN.
   - **Webhooks de CI/CD** (novos): `POST/GET/DELETE /models/{id}/webhooks` (ADMIN) `{url, events: ["version.created","stage.changed","alias.moved"], secret?}`. Entrega assíncrona (job `webhooks.deliver`, 5 tentativas com backoff) com assinatura `X-Matrix-Signature: sha256=<hmac(secret, timestamp + "." + corpo)>` + `X-Matrix-Timestamp`/`-Event`/`-Delivery`. **Toda** requisição sai por `egress_client(purpose="webhook")` (ADR-012; host precisa estar na allowlist do projeto). Segredo gerado pelo servidor quando omitido, mostrado **uma única vez**, guardado cifrado em repouso (Fernet com `MATRIX_SECRET_KEY`; sem a variável o endpoint recusa criar webhook com mensagem clara), nunca devolvido depois. `GET .../deliveries` mostra status, tentativas, último código — sem corpo da resposta.
   - Download de arquivos: `GET /models/{id}/versions/{v}/files` → arquivos do modelo via artifact store do branch (URLs/stream com as mesmas proteções de `api/artifacts.py`).
2. **Linhagem:** hoje é BFS em memória sobre todas as arestas do projeto, sem limite. Implemente consulta limitada `GET /lineage?node=<kind>:<id>&direction=up|down|both&depth=3` (≤ 500 nós, `truncated` sinalizado), com arestas materializadas ou consultas por índice (não carregar o projeto inteiro). Nós: `data_version`/`dataset_commit`, `run`, `model_version`, `experiment`, `sweep`, `benchmark`, `artifact`. Mantenha as rotas `/mlops/runs/{id}/lineage` e `/models/{id}/lineage` funcionando (sobre a nova consulta).
3. **Datasets (router do main com branches/commits/diff/merge/revert + CRUD/from-span/export do branch):** já unidos na T2/T3; verifique os papéis (leitura VIEWER, escrita MEMBER), o custo O(n) de itens copiados por commit (documente o limite e adicione teto configurável de itens por commit) e ligue `runs.dataset_version_id` a um commit de dataset (valide que o commit existe no projeto ao criar o run). `/data-sets` por manifesto de arquivos (g3 §2) fica **fora desta tarefa** — registre como adiado em `t6-notes.md`.
4. Todas as rotas novas com `require(...)`; auditoria (`audit.record`) em promoção, alias, criação/remoção de webhook.

## Migração
Fragmento `server/migrations/fragments/t6_registry.py` (tabelas `model_stage_history`, `model_webhooks`, `webhook_deliveries`, índices de linhagem) + teste de fragmento sobre o esquema da 0008. **Não** crie arquivo em `migrations/versions/` (HANDOFF §Migrações).

## Testes
Production único e histórico (inclusive concorrência no Postgres); papéis por rota; assinatura do webhook verificável (recalcule o HMAC no teste); webhook para host fora da allowlist falha com erro claro; redirect para IP privado bloqueado (use os seams `egress.set_resolver_for_tests`/`set_dialer_for_tests`); segredo nunca aparece em resposta/log; linhagem limitada a 500 nós e profundidade; isolamento entre projetos em todos os endpoints novos.

## Pronto quando
`ruff` limpo; suíte completa verde com bancos reais; `check_skips` aceita; inventário de rotas ok; `t6-notes.md` com decisões e o que ficou adiado.
