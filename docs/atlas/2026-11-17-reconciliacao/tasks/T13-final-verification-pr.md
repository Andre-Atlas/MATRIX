# T13 — Verificação final, relatório e PR (Gate 2)

`[core]` · nível **C2** · Depende de: T12. **Só abra o PR se o usuário pedir.** Leia [HANDOFF.md](../HANDOFF.md).

## Fazer
1. Religar o ambiente (HANDOFF §Ambiente) e rodar toda a verificação (HANDOFF §Verificação): ruff, pytest completo com bancos reais + Docker, `check_skips`, Alembic em banco novo (`upgrade head`, `check`, `downgrade base`, `upgrade head`), SDK, web (`tsc`/`vitest`/`build`), e2e, `make demo`.
2. Atualizar `ledger.md` e `plan.md` (todas as tarefas com status final) e escrever o relatório final em `docs/atlas/2026-11-17-reconciliacao/report.md` (números, decisões, riscos abertos, o que ficou de fora: motores reais, chaves virtuais, `/data-sets` por manifesto, S3/GPU não validados).
3. Gate 2: apresentar o relatório ao usuário com uma pergunta interativa (abrir PR / ajustar / parar). Se aprovado: `git fetch origin main`, confirmar que `origin/main` segue ancestral, abrir PR contra `main` (sem template no repo; rodapé de atribuição) e assinar a atividade do PR.

## Pronto quando
Evidência de cada comando colada no relatório; árvore limpa e branch sincronizado.
