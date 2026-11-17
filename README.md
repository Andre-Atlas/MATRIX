# MATRIX

Plataforma LLMOps de observabilidade: traces OTLP/OpenInference, prompts versionados, avaliação (datasets e experimentos) e fila de anotação, num só lugar.

Spec do MVP: [docs/atlas/2026-11-09-plataforma-mvp/spec.md](docs/atlas/2026-11-09-plataforma-mvp/spec.md)

## Estrutura

- `server/` — pacote `matrix_server` (FastAPI, SQLAlchemy, uv). Processos `matrix-api` e `matrix-worker`.
- `sdk/python/` — pacote `matrix_sdk`.
- `apps/web/` — interface Next.js (App Router, Tailwind).
- `deploy/compose.yaml` — ClickHouse, PostgreSQL, API, worker e web (sem S3: artefatos no volume local; veja `deploy/.env.example`).

## Módulos vindos do `main` (integração em andamento)

O `main` foi integrado neste branch com o branch como base ([ADR-013](docs/adr/013-reconciliacao-main-branch.md)). Os módulos abaixo já estão no código, mas **ainda não foram portados** para o RBAC (`require(...)`), para `runs`/`run_metrics`/`artifacts` nem para as correções de segurança obrigatórias; não use em produção até o fim do ciclo de integração ([plano](docs/atlas/2026-11-17-reconciliacao/plan.md)).

| Módulo | API | Web | Situação |
|---|---|---|---|
| MLOps (treino, sweeps, artefatos, linhagem, registry) — [ADR-014](docs/adr/014-mlops-tracking-e-model-registry.md) | `/api/v1/mlops/*` | `/training`, `/models` | integração em andamento (T3, T4, T5, T6) |
| Versionamento de datasets (branches, commits, diff, merge) — [ADR-015](docs/adr/015-benchmarks-sandboxes-e-data-versioning.md) | `/api/v1/datasets/{id}/branches…` | `/datasets` | integração em andamento (T6) |
| Benchmarks (`local_sim` simulado) | `/api/v1/benchmarks/*` | `/benchmarks` | integração em andamento (T7) |
| Sandboxes | `/api/v1/sandboxes/*` | `/sandboxes` | integração em andamento (T7: Docker obrigatório, falha fechada) |
| Vetores e drift (embedding por hash, simulado) | `/api/v1/vectors/*` | `/vectors` | integração em andamento (T8) |
| Safety / red-team heurístico | `/api/v1/safety/*` | `/safety` | integração em andamento (T8) |
| AI Gateway | `/api/v1/gateway/*` | `/gateway` | integração em andamento (T8) |

## Execução

> **Não validado nesta rodada:** o store S3 real (`MATRIX_ARTIFACT_BACKEND=s3`, `MATRIX_S3_*`) e o
> `docker compose up` completo (não havia Docker disponível). Foram validados por script o YAML do
> compose (`scripts/check_compose.py`), os testes, o `make demo` contra PostgreSQL e ClickHouse reais e
> o artifact store local. Trate a stack via compose e o S3 como não testados até rodá-los.

A web tem login próprio (usuários e sessões, cookie HttpOnly) e **não** guarda chave de API. O compose
publica todas as portas só em `127.0.0.1`; para expor, ponha um proxy reverso com TLS na frente
(e defina `MATRIX_WEB_ORIGIN` com a origem pública `https://...`). A API (8000) fala HTTP puro: exponha-a
só atrás de TLS.

### Desenvolvimento

Pré-requisitos: Python 3.12+, uv, Node 22.

```bash
make dev-api   # API em :8000 (store em memória + SQLite)
make dev-web   # web em :3000
```

### Stack completa via docker compose

1. Copie `deploy/.env.example` para `deploy/.env` (o compose lê esse arquivo sozinho; ele não deve ir
   para o git) e troque todo `change-me`. As senhas entram em URLs: use só caracteres seguros para URL,
   por exemplo `openssl rand -hex 24`. Obrigatórias: `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD` e
   `MATRIX_WEB_ORIGIN` (origem pública da web, ex. `https://matrix.exemplo.com`; o `Origin` de login,
   logout e escritas tem de ser exatamente esta). **Uso local** em HTTP puro: `MATRIX_WEB_ORIGIN=http://localhost:3000`
   e `MATRIX_INSECURE_COOKIES=1` (já repassada ao serviço `web`). `MATRIX_ADMIN_TOKEN`
   (rotas `/api/v1/admin/*`) é opcional: o CLI `matrix-admin` funciona sem ele.

2. Suba a stack (a API aplica `alembic upgrade head` antes de iniciar):

   ```bash
   make up   # docker compose -f deploy/compose.yaml up --build
   ```

3. Crie o primeiro usuário e o projeto (o CLI lê a senha do terminal; ela não vai na linha de comando):

   ```bash
   docker compose -f deploy/compose.yaml exec api matrix-admin create-user voce@exemplo.com --name "Voce"
   # user_id: ...
   docker compose -f deploy/compose.yaml exec api matrix-admin create-project demo --owner-email voce@exemplo.com
   # project_id: ...
   # api_key: mx_...   (guarde: não é mostrada de novo)
   ```

4. Abra a web (`MATRIX_WEB_ORIGIN`, ou `http://127.0.0.1:3000` atrás de túnel/proxy) e entre com esse
   e-mail e senha. Outros membros entram por Settings > Members (o usuário precisa existir:
   `matrix-admin create-user`).

5. A chave `mx_...` é só para o SDK e a ingestão: `MATRIX_API_KEY=mx_... MATRIX_ENDPOINT=http://127.0.0.1:8000`.
   Chaves de API nunca têm papel de admin; regras, avaliações online e configurações do projeto exigem
   uma sessão de usuário admin/owner.

Artefatos de runs ficam no volume `artifacts-data` (`MATRIX_ARTIFACT_BACKEND=local`). O compose não
tem serviço S3: nenhuma imagem S3 foi validada.

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
| `MATRIX_ENV` | `production` | `production`, `dev` ou `test` (só a suíte de testes). Fora de `test`, API e worker não sobem sem `MATRIX_ARTIFACT_TOKEN_SECRET` |
| `MATRIX_ARTIFACT_TOKEN_SECRET` | — (obrigatória fora de `test`) | Chave HMAC dos tokens de upload/download de artefatos do MLOps; o mesmo valor em todos os processos `matrix-api`/`matrix-worker` (≥ 32 caracteres em `production`: `openssl rand -hex 32`) |
| `MATRIX_HOST`, `MATRIX_PORT` | `0.0.0.0`, `8000` | Endereço do `matrix-api` |
| `MATRIX_MAX_BODY_BYTES` | `10000000` | Corpo máximo de qualquer requisição (413 acima) |
| `MATRIX_MAX_JSON_BODY_BYTES` | `2000000` | Corpo máximo em `/api/v1/*` |
| `MATRIX_MAX_ATTRIBUTE_CHARS` | `100000` | Tamanho máximo de um atributo de span |
| `MATRIX_MAX_NORMALIZED_FACTOR` | `4` | Atributos decodificados de uma requisição ≤ fator × `MAX_BODY_BYTES` |
| `MATRIX_MAX_POINTS_PER_RUN`, `MATRIX_MAX_POINTS_PER_KEY` | `50000000`, `10000000` | Pontos de métrica aceitos por run e por chave (*ciclo 2*) |
| `MATRIX_SESSION_IDLE_TTL_SECONDS`, `MATRIX_SESSION_ABSOLUTE_TTL_SECONDS` | 7 dias, 30 dias | Validade das sessões web (*ciclo 2*) |
| `MATRIX_ALLOW_PROJECT_CREATION` | `true` | Qualquer usuário logado pode criar projeto (*ciclo 2*) |
| `MATRIX_TRUSTED_PROXY_HEADER` | — | Cabeçalho com o IP real, sobrescrito por um proxy confiável (entrada mais à direita). Só vale para conexões vindas de `MATRIX_TRUSTED_PROXIES`. Server e web devem usar o mesmo valor (no compose, a mesma variável vai aos dois) |
| `MATRIX_TRUSTED_PROXIES` | — (nunca confia no cabeçalho) | CIDRs/endereços, separados por vírgula, dos proxies cujo `MATRIX_TRUSTED_PROXY_HEADER` é aceito; de qualquer outro peer o cabeçalho é ignorado. No compose, liste a rede interna de onde a web chama a API, por exemplo `172.16.0.0/12,10.0.0.0/8,192.168.0.0/16` (exemplo: restrinja à sua rede). A trava de login por IP só vale com IP real do cliente (cabeçalho aceito ou conexão direta de endereço público); quando a API só vê o IP do proxy (loopback, privado ou da lista), ficam as travas por e-mail+IP e por e-mail |
| `MATRIX_MAX_RUNS_PER_PROJECT`, `MATRIX_MAX_ACTIVE_RUNS_PER_PROJECT` | `100000`, `1000` | Runs por projeto (409 acima) e runs `running` por projeto (429 acima) |
| `MATRIX_PASSWORD_HASH_CONCURRENCY` | `4` | Operações argon2 simultâneas por processo (*ciclo 2*) |
| `MATRIX_ARTIFACT_BACKEND` | `local` | `local` (validado) ou `s3` (**não validado** nesta rodada) |
| `MATRIX_ARTIFACT_LOCAL_DIR` | `./artifacts` | Diretório do store local (no compose, `/app/artifacts`, volume `artifacts-data`) |
| `MATRIX_S3_ENDPOINT`, `MATRIX_S3_PUBLIC_ENDPOINT`, `MATRIX_S3_BUCKET`, `MATRIX_S3_REGION`, `MATRIX_S3_ACCESS_KEY`, `MATRIX_S3_SECRET_KEY`, `MATRIX_S3_AUTO_CREATE_BUCKET`, `MATRIX_S3_TRUST_CHECKSUM` | —, —, `matrix-artifacts`, `us-east-1`, —, —, `false`, `false` | Store S3, opcional e **não validado**; não está no compose |
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
| `MATRIX_MOCK` | — | `1` = dados de exemplo, sem API (desenvolvimento e screenshots) |
| `MATRIX_WEB_ORIGIN` | — (**obrigatória em produção**; no compose, sem ela a stack não sobe) | Origem pública da web (`https://matrix.exemplo.com`, com porta se não for a padrão; local: `http://localhost:3000`). Definida, o `Origin` das mutações (login, logout, troca de projeto, escritas pelo proxy) precisa ser exatamente ela. Vazia: compara esquema + `Host` (esquema do `X-Forwarded-Proto` só com `MATRIX_WEB_TRUST_FORWARDED_FOR=1`; em produção com cookies Secure o esquema esperado é sempre `https`). Recomendada atrás de proxy reverso |
| `MATRIX_TRUSTED_PROXY_HEADER` | — | Nome do cabeçalho que a web sobrescreve com o IP do cliente ao chamar a API (ex. `X-Real-IP`); só com `MATRIX_WEB_TRUST_FORWARDED_FOR=1`. Igual ao do servidor |
| `MATRIX_MOCK_LATENCY_MS` | — | Com `MATRIX_MOCK=1`, atraso artificial por chamada (testes de latência) |
| `MATRIX_WEB_TRUST_FORWARDED_FOR` | — | `1` = há um proxy reverso confiável na frente do Next: usa o último `X-Forwarded-For` como IP do cliente e o `X-Forwarded-Proto` como esquema |
| `MATRIX_INSECURE_COOKIES` | — | `1` = cookies sem `Secure` em produção (só HTTP local). Com Secure os cookies se chamam `__Host-matrix_session` e `__Host-matrix_project`; sem, `matrix_session` e `matrix_project` |

Compose: `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD`, `MATRIX_WEB_ORIGIN` e `MATRIX_ARTIFACT_TOKEN_SECRET` (obrigatórias);
`MATRIX_DATABASE_URL` e `MATRIX_CLICKHOUSE_URL` são montadas a partir das senhas. Exemplo completo em
[`deploy/.env.example`](deploy/.env.example). `make demo` usa PostgreSQL e ClickHouse locais
(`matrix_demo`) e não depende de nenhuma chave de API da web.

## Demo ponta a ponta

`examples/rag_demo.py` simula um agente de suporte com RAG (3 sessões, 8 turnos, com erro de ferramenta, resposta contraditória e feedback negativo) usando só o SDK:

```bash
MATRIX_API_KEY=... MATRIX_ENDPOINT=http://localhost:8000 uv run --project sdk/python python examples/rag_demo.py
```

`make demo` roda `scripts/verify_mvp.py`: sobe API e worker (ClickHouse e PostgreSQL locais, com `alembic upgrade head` já aplicado), cria usuário owner e projeto (token admin), cria regras e avaliação online com a sessão desse usuário (chave de API nunca é admin), executa o demo e confere cada critério do MVP pela API, imprimindo PASS/FAIL. Sai com código 0 só se todos passarem.

## Testes

```bash
make test   # server, sdk e web
make lint   # ruff nos dois pacotes Python e tsc na web
```
