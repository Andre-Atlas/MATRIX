# T10 — SDK Python: unificar `mlops` do main com `matrix_sdk` e remover compat

`[core]` · nível **C3** · ATLAS: sonnet/medium (Antigravity: `pro`) · Depende de: T5, T6. Leia [HANDOFF.md](../HANDOFF.md) e `t4-notes.md`.

## Fazer
1. `sdk/python/src/matrix_sdk/mlops.py` (API do main) vira camada fina deprecated sobre `matrix_sdk.tracking`/sweeps/registry (emitir `DeprecationWarning`); nada de segunda pilha HTTP.
2. Todo `httpx` do SDK respeita as configurações de proxy/CA do ambiente (`trust_env`), sem desabilitar verificação TLS.
3. Registry e linhagem no SDK (`log_model`, `register`, `promote`, `lineage`) sobre as rotas canônicas da T6.
4. Depois que T9 e o SDK não usarem mais `api/mlops_compat.py`/`mlops/compat.py`: remover compat **cuidando** para que a migração 0008 (que importa `mlops/compat.py`) continue funcionando — inline a lógica na migração antes de apagar.

## Pronto quando
`cd sdk/python && uv run ruff check . && uv run pytest -q` verdes (base: 112); testes de depreciação; exemplos em `sdk/python/examples` rodam contra o app em memória.
