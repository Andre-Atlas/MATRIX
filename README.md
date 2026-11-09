# MATRIX

Plataforma LLMOps de observabilidade: traces OTLP/OpenInference, prompts versionados, avaliação (datasets e experimentos) e fila de anotação, num só lugar.

Spec do MVP: [docs/atlas/2026-11-09-plataforma-mvp/spec.md](docs/atlas/2026-11-09-plataforma-mvp/spec.md)

## Estrutura

- `server/` — pacote `matrix_server` (FastAPI, SQLAlchemy, uv). Processos `matrix-api` e `matrix-worker`.
- `sdk/python/` — pacote `matrix_sdk`.
- `apps/web/` — interface Next.js (App Router, Tailwind).
- `deploy/compose.yaml` — ClickHouse, PostgreSQL, MinIO, API, worker e web.

## Desenvolvimento

Pré-requisitos: Python 3.12+, uv, Node 22.

```bash
make dev-api   # API em :8000 (store em memória + SQLite)
make dev-web   # web em :3000
make up        # stack completa via docker compose
```

## Testes

```bash
make test   # server, sdk e web
make lint   # ruff nos dois pacotes Python e tsc na web
```
