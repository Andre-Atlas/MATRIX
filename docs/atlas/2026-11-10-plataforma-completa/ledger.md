# Ledger — MATRIX ciclo 2 (plataforma completa)

## Decisões do usuário

- 2026-11-10: "além do LLMOps, vamos implementar as outras partes em vez de só ir para o MVP" — escopo = todo o restante do blueprint.
- `main` do MATRIX foi criado pelo usuário no commit final do ciclo 1 (Gate 2 do ciclo 1 concluído). O trabalho continua no branch `ccr-9443eae6-fk5867`, que parte desse mesmo commit.

- 2026-11-10 **Gate 1 aprovado** pelo usuário: executar o plano inteiro de forma autônoma (G0→G9), revisão só no fim; usar Inspect AI, lm-eval-harness e VLMEvalKit reais num runner isolado; validar GPU e chaves reais depois (sem GPU nem chaves neste ambiente).

## Rulings

- Ruling: Docker passou a funcionar neste ambiente (daemon iniciado manualmente) — sandboxes do G6 e `docker compose` serão executados de verdade, diferente do ciclo 1 — custo: nenhum.

## Despachos

Formato: `T<n> · [tag] · C<n> → modelo/esforço (papel)`.

- T20 · [arch] · C4 → orquestrador inline (ADR-006/007/012, contrato g0, esqueleto RBAC/auditoria)
- T21 · [core] · C5 → opus/high (implementer) — usuários, sessões, RBAC, escopo de chaves
- T22 · [arch] · C4 → opus/medium (implementer) — artifact store
- T23 · [core] · C4 → opus/medium (implementer) — política de egress
- T25 · [arch] · C4 → orquestrador inline (ADR-008, contrato g1)
- T26 · [arch] · C5 → opus/high (implementer) — runs + séries ClickHouse + M4
- T27 · [core] · C4 → opus/medium (implementer) — SDK de tracking, telemetria, integrações

## Progresso

- Contratos prontos: g0, g1, g2, g3 (em `contracts/`). Próximos a escrever: g4 (vetores/drift), g5 (red-team), g8 (gateway), g6/g7 (runners), g9.

- Gate 1 aprovado; G0 iniciado.

- Ruling: o downsampling de séries usa **M4 dentro do ClickHouse** (ADR-008) em vez de LTTB em Python (que a spec citava) — preserva picos e vales, é exato para gráficos de linha e não traz pontos brutos para a API — custo: nenhum para a UI.
- Ruling: MinIO não está disponível (a imagem `minio/minio` foi retirada do Docker Hub e o `quay.io` exige login); o servidor S3 de testes e do compose passa a ser **RustFS** (imagem `rustfs/rustfs`, validado aqui com boto3, URL pré-assinada PUT/GET e HEAD) — o código usa a API S3 genérica via boto3 — custo: trocar o serviço no compose.
- Ruling: o esqueleto compartilhado (`Role`, `Principal`, `require`, `audit.record`, modelo `AuditLog`) foi escrito antes de despachar T21–T23 para que as três tarefas codem contra a mesma interface; as migrações Alembic de cada fatia são geradas uma vez, pelo orquestrador, no fecho da fatia — custo: nenhum.

- 2026-11-10 (17:20 UTC): o limite de uso de 5 h interrompeu T21, T22, T23, T26 e T28 (trabalho salvo, sem commit; 44 arquivos em `server/` e `sdk/`). O worker reiniciou e Postgres, ClickHouse, Docker e o S3 de teste ficaram desligados.
- Conferência pedida pelo usuário (`audit`, lane Auditoria): produção (sonnet/medium, C3), segurança (opus/medium, C4), falhas silenciosas (sonnet/medium, C3), testes (sonnet/medium, C3), consolidação pelo `audit-lead` (opus/medium, C4). Relatório: `docs/atlas/auditorias/2026-11-10-matrix-conferencia.md` — 0 CRITICAL, 4 HIGH, 24 MEDIUM, 15 LOW.
- Ruling: o ciclo 2 fica **pausado** até o usuário decidir; os 5 agentes interrompidos NÃO foram retomados — o estado real (3 testes falhando em `test_runs_api.py`, 4 erros de ruff) precisa ser revisto antes — custo: tempo.
- Ruling: performance, fluxos de interface, apps com LLM e comentários ficaram fora da auditoria para poupar o limite de uso; decisão do orquestrador, a confirmar com o usuário.
- 2026-11-10: usuário respondeu "corrige os 4 HIGH e retoma os agentes; pode ligar Postgres e ClickHouse". Ligados Postgres e ClickHouse (Docker e S3 não: só esses dois foram autorizados). Baseline com bancos reais: 909 passed, 9 failed (3 testes de `test_runs_api` × 3 backends, todos do T26), 64 skipped.
- Fix 4 HIGH · C4 → opus/medium (implementer; gatilho de segurança: revisão depois)
- Retomados do estado salvo: T21 (opus/high, C5), T22, T23, T26 (opus), T28 (sonnet/medium). Ruling: o S3 real (RustFS) fica sem validar nesta rodada — exige Docker, não autorizado — custo: testes S3 pulam.

- 2026-11-10 (noite): segundo corte por limite de uso (reabriu 22:20 UTC); retomados T21–T28 + fix dos HIGH. Concluídos: **4 HIGH corrigidos** (compose sem MinIO e portas locais, proxy web endurecido + aviso de "sem login", experimento `failed`/parcial, juiz heurístico rotulado), T21 (RBAC, S9), T22 (artifact store, S10/S11), T23 (egress, S12), T26 (runs, M4), T28 (avaliação de ML), ajuste web (`_meta`, `errored`). Migração 0003 gerada (up/check/down/up/down-base provados em Postgres). Suíte do servidor com bancos reais: 1044 passed, 70 skipped (S3 real); web 124; SDK 91.
- T21 · [core] · C5 → opus/high; T22/T23/T26/Fix HIGH · C4 → opus/medium; T28 · C3 → sonnet/medium; ajuste web · C2 → sonnet/low; T24 · [design] · C3 → sonnet/medium; revisão de segurança G0/G1 · C4 → opus/medium (security-reviewer).
- Ruling: `source` do score online continua `"llm"` com `label="heuristic"` e prefixo `[heuristic judge]` no comentário, em vez de acrescentar `heuristic` ao enum do contrato — mantém `api/query.py` compatível — custo: filtros por origem precisam olhar o label.
- Ruling: artefatos presigned grandes sem hash comprovável são recusados em vez de guardados como `verified=false` (S11); `MATRIX_S3_TRUST_CHECKSUM=1` permite confiar no checksum do servidor S3 — custo: uploads grandes por presigned exigem esse ajuste ou upload direto.
- Pendência registrada: S3 real (RustFS) sem revalidar depois de S10/S11 (Docker não foi autorizado); `run_worker` deve chamar `service.ensure_reaper_scheduled` (cli.py era do T21).

## Fechamento do G0 + G1 (plano aprovado em 2026-11-17)

- E1 verificada: servidor com Postgres e ClickHouse reais = **1100 passed, 0 failed, 88 skipped** (os pulados são o backend S3 real, sem Docker); ruff limpo; migração 0004 sobe, `alembic check` limpo, desce a 0003, sobe e desce à base sem erro. Isso confirma o commit `2d68c14` (rotulado `wip`) como a correção definitiva da revisão de segurança do servidor.
- E2 retomada: correção de segurança da web (opus/medium, C4) e T29 telas de runs (opus/medium, C4), arquivos disjuntos.

## Fechamento G0+G1 — verificação final e divergência do main (2026-11-17)

- E2–E5 concluídas: correção de segurança da web, T29 (telas de runs), pontas soltas, revisão E4 (2 HIGH + 4 MEDIUM corrigidos). Verificação final: servidor **1140 passed, 88 skipped** (só S3 e teste de desempenho, aceitos por `scripts/check_skips.py`) com Postgres e ClickHouse reais; SDK 91; web 358 (`tsc`, `next build` ok); `make demo` 6/6; compose validado por `scripts/check_compose.py`; migrações 0001–0004 sobem, `alembic check` limpo, descem à base.
- **Achado antes do PR:** `origin/main` (4d05086) avançou 21 commits de André Acioli depois do commit-base (847221d), em paralelo a este branch: migrações `0003_users_and_members`, `0004_safety_scans`, `0005_mlops_studio`, `0006_sota_completion`; benchmarks (lm-eval/VLMEvalKit), sandboxes (Inspect AI), versionamento de datasets com merge de 3 vias, ADRs 006/007 próprios. Sobreposição: 22 arquivos tocados dos dois lados; **colisão de revisões Alembic (0003/0004)**, tabela `users` definida nos dois, **colisão de numeração de ADR (006/007)**. O branch **não** é descendente do main.
- Ruling: NÃO abrir PR, NÃO rebasear nem mesclar o `main` sem decisão do usuário — a integração decide qual implementação vale por área (efeito sobre código de outra linha de trabalho e sobre o esquema do banco) — custo: PR atrasado até a decisão.
