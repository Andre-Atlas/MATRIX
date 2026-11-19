# ADR-013: reconciliação entre o `main` e o branch do ciclo 2

Status: aceito · 2026-11-17

## Contexto
O `main` e o branch `ccr-9443eae6-fk5867` divergiram de `847221d` e implementaram, em paralelo, partes do mesmo blueprint. O `main` é mais largo (sweeps, registry, datasets com branch/merge, linhagem, vetores, red-team, gateway, sandboxes, benchmarks); o branch é mais profundo e endurecido (login/RBAC, auditoria, egress, artifact store, runs/séries). A comparação está em `docs/atlas/2026-11-17-reconciliacao/analise.md`.

## Decisão
1. **O branch é a base** para identidade (usuários, sessões opacas, argon2id, `memberships`), RBAC (`require`/`Principal`), cookies e proxy da web, auditoria, egress, artifact store, tracking de runs (`runs`, `run_metrics`, M4), SDK de tracking, configuração, compose e CI.
2. **Os módulos do `main` entram por cima**: sweeps, registry, versionamento de datasets, linhagem, vetores, red-team, gateway, sandboxes e benchmarks, portados para `Principal`/`require(...)`, para `runs`/`run_metrics`/`artifacts` e para a cadeia Alembic única.
3. **Correções obrigatórias na entrada** (do que o `main` tinha): sandbox só com Docker e **falha fechada**, com `--memory`/`--cpus`, nome e kill no timeout e mounts por allowlist; segredos de benchmark cifrados e nunca devolvidos; `jwt_secret` removido (tokens de upload assinados com chave própria sem padrão público); nenhum resultado sem rótulo quando for simulado.
4. **Simulação rotulada agora, motores reais depois.** Os módulos cujo motor é próprio (benchmarks `local_sim`, sandbox, vetores por hash, gateway mock, safety heurístico) entram com `simulated=true` visível na API e na UI e atrás de uma interface de motor. Inspect AI, lm-eval-harness, VLMEvalKit e UMAP/HDBSCAN (ADR-009, ADR-010) seguem como o destino, num ciclo seguinte. Esta decisão adia, mas não revoga, aquelas ADRs.
5. **Numeração de ADRs:** 001–013 do branch ficam; os ADRs 006 (`mlops-tracking-e-model-registry`) e 007 (`benchmarks-sandboxes-e-data-versioning`) do `main` passam a **014** e **015**.
6. **Cadeia Alembic única:** 0001, 0002, 0003 e 0004 do branch; depois `safety_scans` (0005), `mlops_studio` (0006, reduzida onde sobrepõe `runs`/`artifacts`) e `sota_completion` (0007).

## Consequências
- O que o `main` tem de próprio nas tabelas `mlops_*` é refeito sobre `runs`, `run_metrics` e `artifacts`; SDK e web do `main` ganham uma camada de compatibilidade.
- Rotas do `main` sem controle de papel deixam de existir: todas passam por `require(...)`.
- O merge é feito no branch (`git merge origin/main`), nunca no `main`, até o Gate 2.
