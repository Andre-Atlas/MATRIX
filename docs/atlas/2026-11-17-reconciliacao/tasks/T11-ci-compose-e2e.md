# T11 — CI, compose e e2e para as áreas novas

`[arch]` · nível **C3** · ATLAS: sonnet/medium (Antigravity: `pro`) · Depende de: T5–T10. Leia [HANDOFF.md](../HANDOFF.md).

## Fazer
1. `.github/workflows/ci.yml`: jobs server/sdk/web/e2e/deploy continuam; o job do servidor roda com Postgres e ClickHouse reais **e Docker** (testes de sandbox); S3 (RustFS) opcional num job próprio, com `MATRIX_TEST_S3_ENDPOINT`. A guarda `scripts/check_skips.py` continua obrigatória e inclui o motivo "Docker daemon not available" (T7).
2. `deploy/compose.yaml` + `scripts/check_compose.py`: variáveis novas (`MATRIX_SECRET_KEY`, `MATRIX_SANDBOX_IMAGES`, chaves de provedor do gateway por nome), sem segredos no arquivo. Serviço S3 de teste (RustFS) só no perfil de dev.
3. E2E Playwright (`--trace off`): fluxo runs → sweep (agente do SDK) → registry → promoção; fluxo com selo "simulado". `make demo` semeia dados que usem as áreas novas.

## Pronto quando
CI verde no branch (conferir com `mcp__github__actions_list`/`get_job_logs`); `python3 scripts/check_compose.py` ok; e2e verde localmente.
