.PHONY: test lint dev-api dev-web up demo

test:
	cd server && uv run pytest -q
	cd sdk/python && uv run pytest -q
	cd apps/web && npm run test

lint:
	cd server && uv run ruff check .
	cd sdk/python && uv run ruff check .
	cd apps/web && npx tsc --noEmit

dev-api:
	cd server && MATRIX_SPAN_STORE=memory MATRIX_DATABASE_URL=sqlite:///./matrix.db uv run matrix-api

dev-web:
	cd apps/web && npm run dev

up:
	docker compose -f deploy/compose.yaml up --build

DEMO_DATABASE_URL ?= postgresql+psycopg://matrix:matrix@localhost:5432/matrix

demo:
	cd server && MATRIX_DATABASE_URL=$(DEMO_DATABASE_URL) uv run alembic upgrade head
	uv run --project server python scripts/verify_mvp.py
