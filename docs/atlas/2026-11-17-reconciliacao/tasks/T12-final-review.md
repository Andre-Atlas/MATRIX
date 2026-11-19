# T12 — Revisão final (auditoria + segurança) antes do Gate 2

`[audit]` · nível **C5** · ATLAS: opus/high via `audit-lead` (Antigravity: `pro`) · Depende de: T5–T11. Leia [HANDOFF.md](../HANDOFF.md) e a lane `atlas-lane-audit`.

## Fazer
1. Auditores paralelos (segurança, falhas silenciosas, produção, performance, testes) sobre o diff `origin/main..HEAD`. Verifique em especial: nenhuma execução no host; nenhum segredo em respostas/logs/banco; todo `httpx` externo passa por `egress_client`; `require(...)` em toda rota (inventário `tests/test_rbac.py`); isolamento por projeto; ausência de mock silencioso.
2. `audit-lead` funde duplicatas, verifica cada CRITICAL/HIGH de forma adversarial e escreve `docs/atlas/2026-11-17-reconciliacao/audit-final.md` com backlog.
3. Corrija CRITICAL/HIGH aqui (um commit por correção, com teste que falha sem ela). MEDIUM/LOW vão para o relatório como pendências.

## Pronto quando
Nenhum CRITICAL/HIGH aberto; relatório escrito; suíte completa reexecutada.
