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

## Demo ponta a ponta

`examples/rag_demo.py` simula um agente de suporte com RAG (3 sessões, 8 turnos, com erro de ferramenta, resposta contraditória e feedback negativo) usando só o SDK:

```bash
MATRIX_API_KEY=... MATRIX_ENDPOINT=http://localhost:8000 uv run --project sdk/python python examples/rag_demo.py
```

`make demo` roda `scripts/verify_mvp.py`: sobe API e worker (ClickHouse e PostgreSQL locais, com `alembic upgrade head` já aplicado), cria projeto, regras e avaliação online, executa o demo e confere cada critério do MVP pela API, imprimindo PASS/FAIL. Sai com código 0 só se todos passarem.

## Testes

```bash
make test   # server, sdk e web
make lint   # ruff nos dois pacotes Python e tsc na web
```
