# MATRIX

Plataforma LLMOps de observabilidade: traces OTLP/OpenInference, prompts versionados, avaliação (datasets e experimentos) e fila de anotação, num só lugar.

Spec do MVP: [docs/atlas/2026-11-09-plataforma-mvp/spec.md](docs/atlas/2026-11-09-plataforma-mvp/spec.md)

## Estrutura

- `server/` — pacote `matrix_server` (FastAPI, SQLAlchemy, uv). Processos `matrix-api` e `matrix-worker`.
- `sdk/python/` — pacote `matrix_sdk`.
- `apps/web/` — interface Next.js (App Router, Tailwind).
- `deploy/compose.yaml` — ClickHouse, PostgreSQL, API, worker e web (o S3/MinIO volta com o artifact store do ciclo 2).

## Execução

> **⚠️ A web ainda não tem login.** O servidor Next injeta a `MATRIX_API_KEY` em toda chamada do
> navegador, então **quem alcança a porta da web (3000) tem leitura e escrita completas no projeto
> dessa chave** (traces, prompts, datasets, experimentos, anotações). Por isso o compose publica
> todas as portas só em `127.0.0.1`. **Não exponha a web fora de uma rede privada** até o login do
> G0 (ciclo 2) entrar. Para acessar de outra máquina, use um túnel SSH
> (`ssh -L 3000:127.0.0.1:3000 servidor`) ou um proxy reverso **com autenticação** (Caddy/nginx com
> TLS e `basic_auth`, ou oauth2-proxy) apontando para `127.0.0.1:3000`; nunca troque o bind para
> `0.0.0.0`. A API (8000) exige chave em toda rota, mas fala HTTP puro: exponha-a só atrás de TLS.

### Desenvolvimento

Pré-requisitos: Python 3.12+, uv, Node 22.

```bash
make dev-api   # API em :8000 (store em memória + SQLite)
make dev-web   # web em :3000
```

### Stack completa via docker compose

1. Crie `deploy/.env` (o compose lê esse arquivo sozinho; ele não deve ir para o git). As senhas são
   obrigatórias e entram em URLs: use só caracteres seguros para URL, por exemplo
   `openssl rand -hex 24`.

   ```dotenv
   # obrigatórias
   POSTGRES_PASSWORD=troque-por-um-valor-aleatorio
   CLICKHOUSE_PASSWORD=troque-por-outro-valor-aleatorio
   # rotas /api/v1/admin/* (vazio = admin desligado; o CLI matrix-admin funciona sem ele)
   MATRIX_ADMIN_TOKEN=
   # chave que a web usa; preenchida no passo 3
   MATRIX_API_KEY=
   # juiz de avaliação (vazio = juiz heurístico offline, só para teste/demo)
   MATRIX_JUDGE_PROVIDER=
   MATRIX_JUDGE_MODEL=
   MATRIX_EMBEDDING_MODEL=
   MATRIX_OPENAI_BASE_URL=
   OPENAI_API_KEY=
   ANTHROPIC_API_KEY=
   ```

2. Suba a stack (a API aplica `alembic upgrade head` antes de iniciar):

   ```bash
   make up   # docker compose -f deploy/compose.yaml up --build
   ```

3. Crie o primeiro projeto e guarde a chave impressa (ela não é mostrada de novo):

   ```bash
   docker compose -f deploy/compose.yaml exec api matrix-admin create-project demo
   # project_id: ...
   # api_key: mx_...
   ```

4. Ponha a chave em `MATRIX_API_KEY` no `deploy/.env` e recrie a web:

   ```bash
   docker compose -f deploy/compose.yaml up -d web
   ```

   Sem `MATRIX_API_KEY`, a web não manda credencial e toda página responde 401.

5. Abra `http://127.0.0.1:3000`. O SDK usa a mesma chave: `MATRIX_API_KEY=mx_... MATRIX_ENDPOINT=http://127.0.0.1:8000`.

### Variáveis de ambiente

Servidor (`matrix-api`, `matrix-worker`, `matrix-admin`); as marcadas com *ciclo 2* pertencem ao
G0/G1 em andamento.

| Variável | Padrão | Efeito |
|---|---|---|
| `MATRIX_DATABASE_URL` | `sqlite:///./matrix.db` | Banco relacional (PostgreSQL em produção: `postgresql+psycopg://...`) |
| `MATRIX_SPAN_STORE` | `memory` | `memory` (dev/teste) ou `clickhouse` |
| `MATRIX_CLICKHOUSE_URL` | `http://localhost:8123` | ClickHouse dos spans; credenciais na URL (`http://usuario:senha@host:8123`) |
| `MATRIX_CLICKHOUSE_DATABASE` | `matrix` | Banco do ClickHouse |
| `MATRIX_ADMIN_TOKEN` | — | Token das rotas `/api/v1/admin/*`; vazio = 403 nelas |
| `MATRIX_HOST`, `MATRIX_PORT` | `0.0.0.0`, `8000` | Endereço do `matrix-api` |
| `MATRIX_MAX_BODY_BYTES` | `10000000` | Corpo máximo de qualquer requisição (413 acima) |
| `MATRIX_MAX_JSON_BODY_BYTES` | `2000000` | Corpo máximo em `/api/v1/*` |
| `MATRIX_MAX_ATTRIBUTE_CHARS` | `100000` | Tamanho máximo de um atributo de span |
| `MATRIX_MAX_NORMALIZED_FACTOR` | `4` | Atributos decodificados de uma requisição ≤ fator × `MAX_BODY_BYTES` |
| `MATRIX_MAX_POINTS_PER_RUN`, `MATRIX_MAX_POINTS_PER_KEY` | `50000000`, `10000000` | Pontos de métrica aceitos por run e por chave (*ciclo 2*) |
| `MATRIX_SESSION_IDLE_TTL_SECONDS`, `MATRIX_SESSION_ABSOLUTE_TTL_SECONDS` | 7 dias, 30 dias | Validade das sessões web (*ciclo 2*) |
| `MATRIX_ALLOW_PROJECT_CREATION` | `true` | Qualquer usuário logado pode criar projeto (*ciclo 2*) |
| `MATRIX_TRUSTED_PROXY_HEADER` | — | Cabeçalho com o IP real, definido por um proxy confiável (*ciclo 2*) |
| `MATRIX_PASSWORD_HASH_CONCURRENCY` | `4` | Operações argon2 simultâneas por processo (*ciclo 2*) |
| `MATRIX_ARTIFACT_BACKEND` | `local` | `local` ou `s3` (*ciclo 2*) |
| `MATRIX_ARTIFACT_LOCAL_DIR` | `./artifacts` | Diretório do store local (*ciclo 2*) |
| `MATRIX_S3_ENDPOINT`, `MATRIX_S3_PUBLIC_ENDPOINT`, `MATRIX_S3_BUCKET`, `MATRIX_S3_REGION`, `MATRIX_S3_ACCESS_KEY`, `MATRIX_S3_SECRET_KEY`, `MATRIX_S3_AUTO_CREATE_BUCKET`, `MATRIX_S3_TRUST_CHECKSUM` | —, —, `matrix-artifacts`, `us-east-1`, —, —, `false`, `false` | Store S3 (*ciclo 2*) |
| `MATRIX_MAX_ARTIFACT_BYTES`, `MATRIX_PROJECT_QUOTA_BYTES` | 5 GiB, 50 GiB | Tamanho máximo de artefato e cota por projeto (*ciclo 2*) |
| `MATRIX_VERIFY_HASH_MAX_BYTES` | 256 MiB | Acima disso o SHA-256 do upload não é recalculado (*ciclo 2*) |
| `MATRIX_ARTIFACT_URL_TTL_SECONDS` | `3600` | Validade das URLs assinadas (*ciclo 2*) |
| `MATRIX_EGRESS_MODE` | `allowlist` | Chamadas de saída: só hosts cadastrados (`allowlist`) ou qualquer host público (`public`) (*ciclo 2*) |
| `MATRIX_EGRESS_ALLOW_PRIVATE` | `false` | Só dev/teste: permite redes privadas e loopback (*ciclo 2*) |
| `MATRIX_EGRESS_ALLOWED_PORTS`, `MATRIX_EGRESS_MAX_REQUEST_BYTES`, `MATRIX_EGRESS_CONNECT_TIMEOUT` | `80,443`, `1000000`, `5.0` | Limites das chamadas de saída (*ciclo 2*) |
| `MATRIX_JUDGE_PROVIDER` | vazio (`fake`) | Juiz de experimentos e avaliações online. Vazio/`fake`: juiz heurístico offline, **só para teste/demo** (o worker avisa no log e os scores saem marcados `heuristic`). `openai` ou `anthropic`: juiz por LLM |
| `MATRIX_JUDGE_MODEL` | — | Modelo do juiz; obrigatório com provider diferente de `fake` |
| `MATRIX_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedder quando o juiz é `openai` |
| `MATRIX_OPENAI_BASE_URL` | API da OpenAI | Endpoint compatível com OpenAI (variantes, juiz e embedder) |
| `MATRIX_JUDGE_BASE_URL` | `MATRIX_OPENAI_BASE_URL` | Endpoint só do juiz/embedder |
| `MATRIX_EXPERIMENT_CONCURRENCY` | `4` | Chamadas simultâneas por experimento (1 a 32) |
| `MATRIX_MAX_EXPERIMENT_CELLS` | `5000` | Máximo de itens × variantes por experimento |
| `MATRIX_PRICES_FILE` | tabela embutida | JSON com preços por 1M tokens para o cálculo de custo |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | — | Chaves dos providers (só por ambiente, nunca gravadas) |

Web (`apps/web`):

| Variável | Padrão | Efeito |
|---|---|---|
| `MATRIX_API_URL` | `http://localhost:8000` | URL da API vista pelo servidor Next |
| `MATRIX_API_KEY` | — | Chave do projeto injetada em toda chamada (**ver o aviso acima**); vazia = sem credencial |
| `MATRIX_MOCK` | — | `1` = dados de exemplo, sem API (desenvolvimento e screenshots) |

Compose: `POSTGRES_PASSWORD` e `CLICKHOUSE_PASSWORD` (obrigatórias); `MATRIX_DATABASE_URL` e
`MATRIX_CLICKHOUSE_URL` são montadas a partir delas.

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
