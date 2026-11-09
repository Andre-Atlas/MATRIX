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

## Experimentos

`POST /api/v1/experiments` grava o experimento (`queued`) e enfileira um job `experiment.run`
(handler em `workers/handlers.py`, lógica em `evals/runner.py`). O worker roda cada item × variante:
renderiza o prompt (`{{var}}` vem de `item.input`; variável ausente vira texto vazio e fica anotada em
`error`; template sem variáveis recebe o input do item como mensagem do usuário), chama o provider e
avalia as métricas. Erro de provider fica na célula (`error`, métricas n/a) e não derruba o experimento.
Resultados são gravados com upsert por (experimento, item, variante), então um job repetido é
idempotente; um experimento `done` é ignorado. Erros sistêmicos ficam no experimento (mensagem
sanitizada) e o job é reagendado; na última tentativa o status vira `failed`. As regressões contra a
baseline são calculadas na leitura (`GET /api/v1/experiments/{id}`).

Variáveis de ambiente (chaves de API só por ambiente, nunca persistidas):

| Variável | Padrão | Efeito |
|---|---|---|
| `MATRIX_JUDGE_PROVIDER` | `fake` | `fake`: juiz heurístico + embedder por hashing, offline. Outro valor (`openai`, `anthropic`): `LLMJudge` nesse provider |
| `MATRIX_JUDGE_MODEL` | — | Obrigatória quando o provider do juiz não é `fake` |
| `MATRIX_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedder `OpenAIEmbedder` quando o juiz é `openai` (com `anthropic`, usa o embedder por hashing) |
| `MATRIX_OPENAI_BASE_URL` | API da OpenAI | URL compatível (vLLM, Ollama) para variantes e juiz `openai`; definida pelo operador, nunca pela API |
| `MATRIX_EXPERIMENT_CONCURRENCY` | `4` | Chamadas simultâneas por experimento (1 a 32) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | — | Chaves dos providers |

## Testes

```bash
uv run ruff check .
MATRIX_TEST_DATABASE_URL=postgresql+psycopg://matrix:matrix@localhost:5432/matrix_test uv run pytest -q
```
