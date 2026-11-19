# Plano — ciclo de integração `main` → branch (branch como base)

Análise: [analise.md](analise.md) · Decisões do usuário (2026-11-17): **(1) o branch é a base**; os módulos do `main` são portados para cima dele com as correções de segurança obrigatórias. **(2) simulação rotulada agora, motores reais depois**: sandboxes, benchmarks, vetores, gateway e red-team entram com `simulated=true` visível na API e na UI e atrás de uma interface de motor; o sandbox só roda com Docker obrigatório (falha fechada).

```
ATLAS · feature · arquitetura+dados+infra+design+auditoria · large → plano, G1, SDD com despacho por complexidade, revisão com segurança, verificação, G2 (PR) · lanes: design, arquitetura, auditoria
```

Regras: trabalho no branch `ccr-9443eae6-fk5867` (merge de `origin/main` nele); commit e push ao fim de cada tarefa; nenhum merge no `main` sem o Gate 2; Postgres e ClickHouse reais para a suíte; **Docker não autorizado**, então o caminho `docker run` do sandbox fica coberto só por testes unitários e declarado "não validado".

## Tabela de custo (Gate 1)

| Tarefa | Tag | Nível | Implementa | Revisa |
|---|---|---|---|---|
| T1 ADR-013 de reconciliação; ADRs do `main` renumerados para 014 e 015 | `[arch]` | C3 | orquestrador | — |
| T2 Merge de `origin/main` no branch: cadeia Alembic única (0003/0004 do branch, 0005 safety, 0006 mlops, 0007 sota), modelos (`users`/`memberships`), `app.py`/`config.py`/`limits.py`, remoção de JWT e PBKDF2; `alembic upgrade/check/downgrade` em Postgres limpo e suíte do branch verde | `[arch]` | C5 | opus/high | opus/high + segurança |
| T3 Portar os routers do `main` para `require(...)`/`Principal` (gateway, mlops, safety, sandboxes, benchmarks, vectors, datasets); tokens de upload sem `jwt_secret` | `[core]` | C4 | opus/medium | opus/medium + segurança |
| T4 Unificar runs, métricas e artefatos: `mlops_training_runs`→`runs`, `training_metrics`→`run_metrics`, `mlops_artifacts` como metadado sobre `artifacts`; SDK `mlops` como camada de compatibilidade sobre `tracking` | `[arch]` | C5 | opus/high | opus/high |
| T5 Sweeps sobre `runs` (motor puro do `main`, contrato g2) + agente no SDK | `[core]` | C4 | opus/medium | opus/medium |
| T6 Registry: `production` único, histórico de estágios, webhooks assinados via `egress_client`; linhagem limitada; router de datasets do `main` com branches/merge + golden | `[core]` | C4 | opus/medium | opus/medium + segurança |
| T7 Sandboxes (Docker obrigatório e falha fechada, `--memory`/`--cpus`, nome e kill no timeout, mounts por allowlist, rede só pelo egress, `simulated`) e benchmarks (segredo mascarado e cifrado, IP validado fixado via egress, `simulated`) | `[arch]` | C4 | opus/medium | segurança opus/medium |
| T8 Gateway (sem mock silencioso, rate limit compartilhado em banco), safety (sem alvo → "não avaliado", não 100) e vetores (rótulo `simulated`/demo) | `[core]` | C4 | opus/medium | segurança opus/medium |
| T9 Web: reconciliar `api.ts`, navegação, login/proxy (os do branch), `types.ts`; páginas do `main` sob o grupo `(app)` com papéis, selos `simulated`; mock | `[design]` | C4 | opus/medium | opus/medium |
| T10 SDK: `__init__`, `client`, exports e compatibilidade; testes | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T11 CI/compose/e2e: `verify_mvp` cobrindo os módulos novos, guarda de skips, job deploy | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T12 Revisão final (segurança + código) de todo o diff e correções | `[audit]` | C4 | opus/medium | `audit-lead` opus/medium |
| T13 Verificação final, relatório e PR (Gate 2) | `[core]` | C3 | orquestrador | — |

Distribuição: 2 tarefas C5 (opus/high), 7 C4, 3 C3, 1 inline. Esforço nunca acima de `high`; duas falhas na mesma tarefa sobem um nível.

## Ordem e dependências

T1 → T2 (base de tudo) → T3 → T4 → (T5 ∥ T6 ∥ T7 ∥ T8) → (T9 ∥ T10) → T11 → T12 → T13.
T3 e T4 são sequenciais porque tocam `models.py`, `app.py` e `storage/`; T5 a T8 ficam em módulos disjuntos e podem rodar em paralelo (2 a 3 por vez, por causa do limite de uso).

## Gatilhos de segurança

T2 (auth, migração de usuários), T3 (auth/rotas), T6 (webhooks → SSRF), T7 (execução de código, segredos, rede), T8 (chaves de provedor, rate limit). `security-reviewer` roda em todas.

## Critérios de pronto

1. Cadeia Alembic única, `upgrade head`, `alembic check` limpo, `downgrade base` em Postgres limpo.
2. Suíte do servidor com Postgres e ClickHouse reais verde (os 1140 do branch + os ~528 do `main` portados) e `check_skips` aceitando só S3, perf e Docker.
3. Nenhum router sem `require(...)`/`require_user`/`require_admin` (o teste de inventário de rotas do branch cobre os módulos novos).
4. Todo resultado simulado traz `simulated=true` na API e selo na UI; nenhuma rota devolve segredo.
5. `make demo` 6/6 e roteiros Playwright (login, runs, e os módulos novos em modo mock) passando.
6. Revisão de segurança final sem HIGH aberto.
7. PR do branch contra `main`, com o relatório do que não foi validado (Docker real do sandbox, S3 real, GPU, motores reais).

## Riscos

- **Limite de uso** (cortou 5 vezes): commit `wip` por tarefa, retomada por `SendMessage` pedindo `git diff` antes de reler; no máximo 3 agentes em paralelo.
- **`main` continua andando** (é a linha de trabalho do usuário): `git fetch` antes de cada tarefa e antes do PR; se aparecer commit novo, ele é incorporado na T2 ou reportado.
- **Tamanho:** o merge toca ~290 arquivos; por isso T2 vem antes e a suíte do branch precisa ficar verde a cada tarefa.

## Status (2026-11-19 — pausado no branch `feat/reconciliacao-t5-t13`)

- **Feitas e verificadas:** T1, T2, T3, T4, **T5**, **T6**, **T7**, **T8**, migração **`0009`** (provada em PostgreSQL 15: `upgrade head`, `alembic check`, `downgrade base`, `upgrade head`), **T10 (SDK Python)**.
- **Em andamento (WIP salvo em commit):** **T9 (Web)** — telas de Sweeps, Registry/Webhooks, Lineage limitada, selos `Simulado` e `Não avaliado` e migração para API canônica escritas; falta finalizar `tsc --noEmit`, `vitest run`, `next build`, `t9-notes.md` e então remover `api/mlops_compat.py` (inlining `mlops/compat.py` na `0008`).
- **Pendentes:** finalização de T9 + remoção `mlops_compat`, T11 (CI/compose/e2e), T12 (revisão final), T13 (verificação final e Gate 2).

