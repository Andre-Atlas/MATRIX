# HANDOFF — passagem de bastão para o Antigravity

Data: 2026-11-19 · Repo `Andre-Atlas/MATRIX` · Branch de trabalho: **`ccr-9443eae6-fk5867`** (continuar nele) · Base de integração: este branch; `origin/main` (`4d05086`) já é ancestral dele (merge feito na T2).
**Não abrir PR nem mexer no `main` sem o usuário pedir (Gate 2 é dele).**

## 1. Onde estamos
Objetivo: plataforma unificada MLOps/LLMOps/RAG/AgentOps (documento SOTA), orquestrada pelo ATLAS (`atlas-maestro`, `atlas-dispatch` C1–C5, gates, ledger).

| Tarefa | Estado |
|---|---|
| T1 ADR-013, T2 merge do `main` + cadeia Alembic, T3 routers do main no RBAC, T4 runs/métricas/artefatos unificados + migração 0008 | **feitas e verificadas** |
| T5 sweeps, T6 registry/linhagem/datasets, T7 sandboxes+benchmarks, T8 gateway/safety/vetores | **por fazer** (T5/T7/T8 foram cortadas pelo limite de uso antes de gravar qualquer arquivo) |
| T9 web, T10 SDK, T11 CI/compose/e2e, T12 revisão final, T13 verificação+relatório+PR | por fazer |

Especificações autocontidas: [`tasks/T5…T13`](tasks/). Contexto: `analise.md`, `plan.md`, `t2-notes.md`, `t4-notes.md`, `docs/adr/013-reconciliacao-main-branch.md`; histórico de decisões em `docs/atlas/2026-11-10-plataforma-completa/ledger.md`.

Últimos números (HEAD antes desta entrega `6e5a671`): servidor **1359 passed / 88 skipped (S3 e perf) / 0 falhas** com Postgres e ClickHouse reais; SDK 112; web 494 (`tsc` e `next build` ok); migrações 0001–0008 sobem, `alembic check` limpo, descem à base.

## 2. Decisões travadas (não reabrir)
- O branch é a base; módulos do `main` entram por cima **com** correções de segurança.
- **Simulação rotulada agora (`simulated=true`), motores reais depois** (Inspect AI, lm-eval, VLMEvalKit, UMAP/HDBSCAN: só interfaces/stubs `EngineUnavailable`).
- Sandbox só com Docker, **falha fechada**; nunca executar no host.
- ADRs vindos do `main` ficam renumerados 014/015. Cadeia Alembic única.
- Esforço de subagente nunca acima de `high`. Perguntas ao usuário: sempre pela ferramenta interativa de perguntas com opções (regra no `CLAUDE.md`; no Antigravity, `ask_question`).

## 3. Ordem e dependências
T5, T7, T8 (e T6) são paralelizáveis (módulos disjuntos) → T9 + T10 → T11 → T12 → T13. Níveis e modelos em cada arquivo de tarefa. No Antigravity: `haiku→flash`, `sonnet/opus→pro`; esforço não tem equivalente.

## 4. Regra de ouro das migrações
Agentes em paralelo **não** criam arquivos em `server/migrations/versions/`. Cada tarefa entrega um **fragmento** `server/migrations/fragments/<tarefa>_*.py` (`upgrade()`/`downgrade()`, padrão de `g1_runs.py` + teste que aplica o fragmento sobre o esquema da 0008). Quem coordena monta as revisões 0009+ e prova a cadeia: `upgrade head` → `alembic check` → `downgrade base` → `upgrade head` em banco novo. Head atual: `20261117_0008_mlops_runs_into_runs.py`. A 0008 importa `mlops/compat.py` — não apague sem inline (T10).

## 5. Religar o ambiente (container efêmero; tudo estava desligado)
```bash
pg_ctlcluster 16 main start                      # Postgres; bancos matrix_test*
# ClickHouse: dados em /var/lib/chtest
clickhouse server -- --path=/var/lib/chtest &    # ajuste flags conforme o ledger (E0)
rm -f /var/run/docker.sock; nohup dockerd >/tmp/dockerd.log 2>&1 &   # Docker manual
# S3 de teste (opcional): imagem rustfs/rustfs, --user root, volume nomeado
```
Variáveis de teste: `MATRIX_TEST_*` (URL do Postgres com `postgresql+psycopg://`; ClickHouse; `MATRIX_TEST_S3_ENDPOINT` só se houver S3). Os comandos exatos usados estão no `ledger.md` (procure "E0"/"Docker"). Em outro ambiente, use docker compose de `deploy/compose.yaml`.
Observação específica do ambiente de nuvem anterior: builds Docker exigiam injetar a CA do proxy (`/root/.ccr/ca-bundle.crt`) numa **cópia** do Dockerfile; `docker pull` pode dar 429 (repetir). Não se aplica a máquina normal.

## 6. Como verificar (antes de cada commit/entrega)
```bash
cd server && uv run ruff check . && uv run pytest -q          # com bancos reais ligados
python3 scripts/check_skips.py <saida-do-pytest>              # guarda de skips
cd sdk/python && uv run ruff check . && uv run pytest -q
cd apps/web && npx tsc --noEmit && npx vitest run && npx next build
python3 scripts/check_compose.py
make demo                                                     # smoke ponta a ponta
```
Migrações: `alembic upgrade head && alembic check && alembic downgrade base && alembic upgrade head` em banco novo (use `postgresql://`, o `psql` não entende `postgresql+psycopg://`).

## 7. Armadilhas aprendidas
- Limite de uso cortou agentes ~6 vezes: **commit por tarefa**; ao retomar, peça `git diff`/`git status` antes de refazer.
- Nunca `git add -A` com agentes em paralelo; adicione por caminho. Varra segredos antes de commitar.
- Next: `<Suspense>` sem `key` com `searchParams` trava a navegação; não usar `loading.tsx`.
- Testes S3 pulam sem `MATRIX_TEST_S3_ENDPOINT`; sem MinIO (imagens retiradas) use RustFS.
- Migração autogerada com `drop_constraint(None…)`: nomeie a FK.

## 8. Riscos abertos (de `t2-notes.md` / `t4-notes.md`)
Blobs antigos do `main` sem `artifact_id` (download-url responde 410); `training_metrics` do ClickHouse não copiada pela 0008 (SQL nas notas); `mlops_compat`/`views` a remover em T9/T10; `httpx` sem proxy settings no SDK `mlops` e no runner de benchmarks; apagar projeto deixa tabelas sem FK (coberto por varredura por `project_id`). Não validados: S3 real após S10/S11, GPU/NVML, motores reais, `docker compose up` completo.

## 9. Como abrir o PR (Gate 2, só se o usuário pedir)
`git fetch origin main` e confirmar que segue ancestral; PR do branch `ccr-9443eae6-fk5867` contra `main`; o repo não tem template de PR; relatório de `T13` no corpo; nada de force-push.
