# matrix-server

Servidor da MATRIX (FastAPI + SQLAlchemy). Comandos: `matrix-api`, `matrix-worker`, `matrix-admin`.

## Banco de dados

- Os metadados (projetos, chaves, prompts, datasets, experimentos, anotações, regras, alertas e a
  fila `jobs`) ficam no PostgreSQL; testes e modo dev usam SQLite. A URL vem de `MATRIX_DATABASE_URL`.
- **Produção:** o esquema é gerenciado pelo Alembic. Antes de subir API e worker, rode:

  ```bash
  MATRIX_DATABASE_URL=postgresql+psycopg://usuario:senha@host:5432/matrix uv run alembic upgrade head
  ```

- **Testes e dev:** `init_db` (`create_all`) cria as tabelas na hora; não use em produção.
- Mudou um modelo em `storage/models.py`? Gere uma revisão, revise o arquivo e confira que nada ficou
  de fora:

  ```bash
  uv run alembic revision --autogenerate -m "descricao"
  uv run alembic check
  ```

## Fila de jobs

`storage/jobs.py` implementa a fila (ADR-003): `enqueue` grava o job na transação de quem chama;
o worker (`matrix-worker`) usa `claim` (`FOR UPDATE SKIP LOCKED` no PostgreSQL, `UPDATE`
condicional no SQLite), executa o handler registrado com `@register(kind)` em `workers/runner.py` e
marca `done`, ou reagenda com espera de `2**tentativas` segundos até `max_attempts`. Jobs `running`
com lease vencido são retomados. A entrega é pelo menos uma vez: handlers devem ser idempotentes.

## Testes

```bash
uv run ruff check .
MATRIX_TEST_DATABASE_URL=postgresql+psycopg://matrix:matrix@localhost:5432/matrix_test uv run pytest -q
```
