# matrix-server

Servidor da MATRIX (FastAPI + SQLAlchemy). Comandos: `matrix-api`, `matrix-worker`, `matrix-admin`.

> A web tem login próprio e não guarda chave de API; o compose publica as portas só em `127.0.0.1`.
> **Não validados nesta rodada:** o store S3 real e o `docker compose up` completo. Passo a passo de
> bootstrap (`matrix-admin create-user`, `create-project --owner-email`, login pela web) e a tabela
> completa das variáveis `MATRIX_*`: [README da raiz, seção Execução](../README.md#execução).

## IP do cliente e trava de login

- `MATRIX_TRUSTED_PROXY_HEADER` (ex. `X-Real-IP`) só é lido quando o peer da conexão está em
  `MATRIX_TRUSTED_PROXIES` (CIDRs separados por vírgula; vazio, o padrão, = nunca). No compose, a web
  chama a API pela rede interna: liste-a, por exemplo `172.16.0.0/12,10.0.0.0/8,192.168.0.0/16`
  (exemplo; restrinja à sua rede).
- A trava por IP (30 falhas) só vale com o IP real do cliente: cabeçalho aceito de um proxy
  confiável, ou conexão direta de um endereço público. Se a API só vê o proxy (peer loopback,
  privado ou da lista, sem cabeçalho aceito), valem só as travas por e-mail+IP e por e-mail; assim 30
  falhas anônimas não travam o login de todos.

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

- **Lease:** enquanto o handler roda, uma thread renova o lease (`jobs.heartbeat`) a cada
  `lease / 3`; um job longo não é retomado por outro worker. Se a renovação descobre que o job não é
  mais deste worker, a flag `job_lost()` (`workers/context.py`) liga; o runner de experimentos para
  as células pendentes e o resultado é descartado.
- **Falha definitiva:** erro na última tentativa, falha sem retry ou lease vencido após a última
  tentativa chamam os hooks `@on_final_failure(kind)` (`workers/runner.py`). O de `experiment.run`
  marca o experimento `failed` com erro sanitizado.
- **Hooks de score no worker:** `matrix-worker` monta o span store e instala o hook de regras em
  `on_score_created` (`matrix_server/hooks.py`, o mesmo módulo que `create_app` usa). Scores, anotações
  e alertas criados por um job são gravados na transação do job.

## Experimentos

`POST /api/v1/experiments` grava o experimento (`queued`) e enfileira um job `experiment.run`
(handler em `workers/handlers.py`, lógica em `evals/runner.py`). O worker roda cada item × variante:
renderiza o prompt (`{{var}}` vem de `item.input`; variável ausente vira texto vazio e fica anotada em
`error`; template sem variáveis recebe o input do item como mensagem do usuário), chama o provider e
avalia as métricas. Erro de provider fica na célula (`error`, métricas n/a) e não derruba o experimento,
mas é contado: `summary._meta` traz `cells_total`, `cells_failed`, `partial` (alguma célula falhou)
e `judge` (`heuristic` ou `llm`). Se **todas** as células falharem, o experimento termina `failed`
com `error` = `all N cells failed: <primeiro motivo>` (o job termina com sucesso, sem novas
tentativas). Nas regressões, uma célula com erro cuja baseline tinha resultado vira regressão com
`errored: true`. Métricas de juiz (`faithfulness`, `answer_relevancy`, `context_precision`,
`g_eval`) gravam `details.judge` em cada célula; nas avaliações online, o score do juiz heurístico
sai com `label: "heuristic"` e o comentário prefixado com `[heuristic judge]`.
Resultados são gravados com upsert por (experimento, item, variante), então um job repetido é
idempotente; um experimento `done` é ignorado. Erros sistêmicos ficam no experimento (mensagem
sanitizada) e o job é reagendado; na última tentativa o status vira `failed`. As regressões contra a
baseline são calculadas na leitura (`GET /api/v1/experiments/{id}`). Limites na criação (422): itens ×
variantes ≤ `MATRIX_MAX_EXPERIMENT_CELLS`, até 10 variantes e 20 métricas.

Padrões de usuário (métrica `regex` e `pattern` em schemas do `is_json`) rodam no pacote `regex` com
tempo máximo de 1 s por avaliação; estourou, a métrica falha com `regex timeout`. Schemas com
`patternProperties` são recusados (422), porque o jsonschema avalia essa palavra com `re` sem limite.

Variáveis de ambiente (chaves de API só por ambiente, nunca persistidas):

| Variável | Padrão | Efeito |
|---|---|---|
| `MATRIX_JUDGE_PROVIDER` | `fake` | `fake`: juiz heurístico + embedder por hashing, offline, **só para teste/demo**; sem a variável o worker loga um aviso (uma vez por processo). Outro valor (`openai`, `anthropic`): `LLMJudge` nesse provider |
| `MATRIX_JUDGE_MODEL` | — | Obrigatória quando o provider do juiz não é `fake` |
| `MATRIX_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedder `OpenAIEmbedder` quando o juiz é `openai` (com `anthropic`, usa o embedder por hashing) |
| `MATRIX_OPENAI_BASE_URL` | API da OpenAI | URL compatível (vLLM, Ollama) para o provider `openai` em variantes, juiz e embedder, tanto nos experimentos quanto nas avaliações online; definida pelo operador, nunca pela API |
| `MATRIX_JUDGE_BASE_URL` | `MATRIX_OPENAI_BASE_URL` | Opcional: outra URL só para juiz e embedder (experimentos e avaliações online) |
| `MATRIX_EXPERIMENT_CONCURRENCY` | `4` | Chamadas simultâneas por experimento (1 a 32) |
| `MATRIX_MAX_EXPERIMENT_CELLS` | `5000` | Máximo de itens × variantes por experimento |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | — | Chaves dos providers |

## Limites de requisição

Um middleware ASGI (`limits.py`) limita o corpo de toda requisição: `MATRIX_MAX_BODY_BYTES`
(padrão 10 MB) em geral e `MATRIX_MAX_JSON_BODY_BYTES` (padrão 2 MB) em `/api/v1/*`. Vale o
`Content-Length` declarado e a contagem do corpo em streaming (chunked); acima do limite, 413. O
ingest OTLP mantém o próprio limite sobre o corpo descomprimido. `GET /api/v1/datasets/{id}` pagina os
itens (`limit` 1 a 500, padrão 500; `cursor`; resposta `{dataset, items, next_cursor}`, com
`dataset.item_count` igual ao total).

## Testes

```bash
uv run ruff check .
MATRIX_TEST_DATABASE_URL=postgresql+psycopg://matrix:matrix@localhost:5432/matrix_test uv run pytest -q
```
