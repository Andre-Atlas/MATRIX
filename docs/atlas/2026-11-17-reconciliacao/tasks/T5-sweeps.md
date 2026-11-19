# T5 — Sweeps sobre `runs` (grid, random, TPE, poda ASHA/Hyperband, importância) + agente no SDK

`[core]` · nível **C4** · ATLAS: opus/medium (Antigravity: `pro`) · Depende de: T4 (concluída). Pode rodar em paralelo com T7 e T8 (módulos disjuntos).
Leia primeiro: [HANDOFF.md](../HANDOFF.md) (regras comuns, ambiente, verificação), `docs/atlas/2026-11-10-plataforma-completa/contracts/g2.md` (**contrato canônico**), `docs/adr/013-reconciliacao-main-branch.md`, `analise.md` (área B, linha "Sweeps"), `t4-notes.md`.

## Objetivo
Entregar `/api/v1/sweeps` conforme o contrato g2 em cima da pilha de runs do branch (`runs`, `run_metrics`), reaproveitando o motor do `main`, e o agente de sweep no SDK.

## Código de partida
- Motor do main: `server/src/matrix_server/mlops/sweeps.py` (~658 linhas: grid/random/"bayes", early stop median/ASHA, importância), rotas `/mlops/sweeps*` (adaptadores da T4 em `api/mlops_compat.py` e `api/mlops.py`), `tests/test_mlops_sweeps.py`, SDK `sdk/python/src/matrix_sdk/mlops.py` (`create_sweep`, `run_sweep_agent`).
- Pilha canônica: `server/src/matrix_server/runs/` (`service.py`, `store.py`, `models.py`). `runs.sweep_id` e o índice `ix_runs_project_sweep` já existem.

## Fazer
1. **API canônica** exatamente como g2: criar (valida o espaço), listar, obter (runs, melhor, importância), `POST /sweeps/{id}/next` (nunca repete configuração sob concorrência), `POST /sweeps/{id}/runs/{run_id}/report {step,value}` → `{action: continue|stop}`, `PATCH` pausar/retomar/parar, `DELETE` (admin). Papéis: leitura VIEWER, criar/`next`/`report`/parar MEMBER, apagar ADMIN, tudo por `require(...)`; o teste de inventário de rotas (`tests/test_rbac.py`) deve continuar passando. O run criado por `next` é um run do branch com `sweep_id`, `params` fixados e a métrica-alvo em `run_metrics`.
2. **Motor:** métodos `grid`, `random`, `tpe` (TPE univariado: quantil γ=0,25, Parzen gaussiano/categórico, 24 candidatos, reprodutível por semente+histórico). `bayes` do main vira **alias de `tpe`** (ou fica só no adaptador deprecated; decida e registre). Poda ASHA/Hyperband assíncrona com degraus `min_iter·eta^k` e atribuição round-robin a brackets; nunca poda antes de `min_iter`. Importância por floresta aleatória (scikit-learn, extra `analytics`); sem scikit-learn → `importance: null` com nota.
3. **Tabela:** `mlops_sweeps` → `sweeps` (ou mantenha o nome se renomear custar mais que vale; registre a decisão). Campos conforme g2 (config JSON validado, status, métrica/objetivo, melhor run/valor, seed, created_by).
4. **Adaptadores deprecated** `/api/v1/mlops/sweeps*` sobre o serviço novo, com as formas de resposta do main (`deprecated=True` + cabeçalho `Deprecation: true`); `should_stop` no ingest de métricas do adaptador de runs consulta a poda do sweep.
5. **SDK:** `matrix_sdk.sweep(config, name)` → id; `matrix_sdk.agent(sweep_id, function, count, agent_id=None)` (laço next→função→next; erro de um run ⇒ marca `failed` e segue); `run.config` com os params entregues; `run.should_stop()`. `create_sweep`/`run_sweep_agent` do SDK `mlops` viram wrappers deprecated.
6. Ajuste `tests/test_mlops_sweeps.py` do main à nova realidade **sem apagar testes** (troque o que testava comportamento descartado e justifique em `t5-notes.md`).
Web fica para a T9.

## Migração
**Não** crie arquivo em `server/migrations/versions/`. Escreva um **fragmento** `server/migrations/fragments/t5_sweeps.py` com `upgrade()`/`downgrade()` (padrão de `g1_runs.py` e `tests/test_runs_migration.py`), incluindo a cópia de dados de `mlops_sweeps` e um teste que aplica o fragmento sobre o esquema da 0008 com dados semeados. Quem coordena monta a revisão 0009+ ao fim da frente (ver HANDOFF §Migrações).

## Testes que devem existir
Grid esgota sem repetir; random respeita tipos/limites/`log`/`q`; TPE converge numa quadrática 2D melhor que random com a mesma semente em N trials; ASHA não poda antes de `min_iter` e mantém ~1/eta no degrau; 10 chamadas paralelas a `next` ⇒ 10 configurações distintas (também no Postgres); agente+poda ponta a ponta no SDK (app em memória).

## Pronto quando
`ruff check .` limpo (server e sdk); suíte completa do servidor com bancos reais sem falhas nem erros de coleta; `cd sdk/python && uv run pytest -q` verde; `python3 scripts/check_skips.py` aceita a saída; inventário de rotas ok; `t5-notes.md` com decisões (bayes→tpe, nome da tabela) e arquivos tocados.
