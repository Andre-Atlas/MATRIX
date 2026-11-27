.PHONY: test lint dev-api dev-web up demo llm-up llm-pull

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

# Local model server (ADR-016): only the ollama service of the `llm` profile, then the default models.
COMPOSE ?= docker compose -f deploy/compose.yaml
LOCAL_CHAT_MODEL ?= qwen2.5:0.5b
LOCAL_EMBED_MODEL ?= all-minilm
LOCAL_VISION_MODEL ?= moondream

llm-up:
	$(COMPOSE) --profile llm up -d --wait ollama

llm-pull:
	for m in $(LOCAL_CHAT_MODEL) $(LOCAL_EMBED_MODEL) $(LOCAL_VISION_MODEL); do \
		$(COMPOSE) --profile llm exec -T ollama ollama pull $$m || exit 1; \
	done

DEMO_DATABASE_URL ?= postgresql+psycopg://matrix:matrix@localhost:5432/matrix

demo:
	cd server && MATRIX_DATABASE_URL='$(DEMO_DATABASE_URL)' uv run alembic upgrade head
	MATRIX_DATABASE_URL='$(DEMO_DATABASE_URL)' uv run --project server python scripts/verify_mvp.py
