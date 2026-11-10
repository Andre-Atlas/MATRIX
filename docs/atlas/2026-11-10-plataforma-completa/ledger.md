# Ledger — MATRIX ciclo 2 (plataforma completa)

## Decisões do usuário

- 2026-11-10: "além do LLMOps, vamos implementar as outras partes em vez de só ir para o MVP" — escopo = todo o restante do blueprint.
- `main` do MATRIX foi criado pelo usuário no commit final do ciclo 1 (Gate 2 do ciclo 1 concluído). O trabalho continua no branch `ccr-9443eae6-fk5867`, que parte desse mesmo commit.

- 2026-11-10 **Gate 1 aprovado** pelo usuário: executar o plano inteiro de forma autônoma (G0→G9), revisão só no fim; usar Inspect AI, lm-eval-harness e VLMEvalKit reais num runner isolado; validar GPU e chaves reais depois (sem GPU nem chaves neste ambiente).

## Rulings

- Ruling: Docker passou a funcionar neste ambiente (daemon iniciado manualmente) — sandboxes do G6 e `docker compose` serão executados de verdade, diferente do ciclo 1 — custo: nenhum.

## Despachos

Formato: `T<n> · [tag] · C<n> → modelo/esforço (papel)`.

## Progresso

- Gate 1 aprovado; G0 iniciado.

