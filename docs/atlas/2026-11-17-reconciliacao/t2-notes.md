# T2 — merge de `origin/main` (4d05086) no branch: notas

Merge: `0883369` (`merge: origin/main (4d05086) no branch, branch como base (ADR-013)`), seguido de commits de ajuste. Nada foi enviado ao `main`.

## Decisões por arquivo em conflito

| Arquivo | Decisão |
|---|---|
| `server/src/matrix_server/auth.py` | Branch inteiro + bloco `# TEMPORARY compat for main's routers — removed by T3`: `AuthContext` (project_id, user_id, role, scopes, is_api_key, user_email; derivado do `Principal`), `require_role("viewer"/"member"/"admin"/"owner")` = `require(Role.X)` (carrega o `.rbac` do `require`), `current_auth_context` = `require(Role.VIEWER)`. Sem JWT, PBKDF2, `create_jwt`, `decode_jwt`, `current_project_id`. |
| `server/src/matrix_server/api/auth.py` | Branch (descartados `POST /auth/register`, login JWT e gestão de `project_members` do `main`). |
| `server/src/matrix_server/storage/models.py` | União. `User`/`Membership` do branch; `User`/`ProjectMember`/`Project.members` do `main` removidos; alias `ProjectMember = Membership` (temporário). A classe `Artifact` do `main` (tabela `mlops_artifacts`) virou `MlopsArtifact` para não colidir no registry com `artifacts.models.Artifact`; alias `Artifact = MlopsArtifact` só como nome Python (T4 remove). |
| `server/src/matrix_server/app.py` | Routers dos dois lados; `app.state`: `metric_store`, `artifact_store` (branch, lazy em `api/artifacts.py`), `training_metric_store`, `gateway_router` e **`mlops_artifact_store`** (o store do `main` ganhou outro nome porque `artifact_store` já é o do branch; `api/mlops.py` e `api/sandboxes.py` ajustados). |
| `server/src/matrix_server/config.py` (auto-merge, corrigido) | Sem `jwt_secret`/`jwt_exp_hours`; um só conjunto de artefatos (nomes do branch: `artifact_backend`, `artifact_local_dir`, `s3_*`, `max_artifact_bytes` = 5 GiB, `artifact_url_ttl_seconds`). Aliases só de leitura: `artifact_store`→`artifact_backend`, `artifact_dir`→`artifact_local_dir/mlops`, `artifact_presign_ttl_seconds`→`artifact_url_ttl_seconds`. Nova `artifact_token_secret` (SecretStr, sem padrão; sem ela, chave aleatória por processo) assina os tokens do store local do `main`. |
| `server/src/matrix_server/mlops/artifact_store.py` | Chave HMAC sai de `jwt_secret` para `artifact_token_secret`/aleatória; S3 tolera `s3_endpoint`/credenciais `None` do branch. |
| `server/src/matrix_server/limits.py` | Tratamento do branch (`RAW_UPLOAD_ROUTES`), com a rota `PUT /api/v1/mlops/artifacts/local/{token}` do `main` incluída na lista. |
| `server/src/matrix_server/api/datasets.py` | Router do `main` (branches/commits/diff/merge/revert, `/items` com mídia) + CRUD/items/from-span/export do branch. Leitura `require(VIEWER)`, escrita `require(MEMBER)` (via `project_of` ou compat). |
| `server/src/matrix_server/workers/handlers.py` | União (purge de projeto do branch + jobs `benchmark.run`/`mlops.evaluate`/`sandbox.run` do `main`). |
| `server/migrations/versions/*` | 0001–0004 do branch; `safety_scans` → `20261113_0005` (`down=0004`), `mlops_studio` → `20261113_0006`, `sota_completion` → `20261114_0007`; `0003 users_and_members` do `main` apagada. Nenhuma migração do `main` tinha FK para `users`/`project_members`. |
| `deploy/compose.yaml` | Branch (sem MinIO `minioadmin`, portas em 127.0.0.1, senhas obrigatórias). |
| `apps/web/next.config.mjs` | Apagado (o branch usa `next.config.ts`). |
| `apps/web/src/app/api/proxy/[...path]/route.ts`, `login/page.tsx`, `lib/api.ts`, `lib/proxy-client.ts`, `app/layout.tsx`, `components/route-error.tsx` | Branch. `api.ts` ganhou só os helpers puros `cleanBaseUrl`/`buildApiUrl` do `main` (testados em `api.test.ts`); `proxy()` aceita `PUT`. |
| `apps/web/src/app/(app)/layout.tsx` | Nav do branch + links Training, Models, Benchmarks, Sandboxes, Vector Space, Safety & OWASP, AI Gateway. Páginas do `main` movidas para `app/(app)/…`. |
| `apps/web/src/lib/mock-api.ts` | União; login JWT falso do mock do `main` removido (o branch tem `mock-auth`). |
| `apps/web/src/lib/types.ts` | União (sem nomes duplicados). |
| `apps/web/src/components/auth/user-nav.tsx` (do `main`) | Apagado: sem uso e gravava `matrix_session`/`matrix_project_id` por JavaScript. |
| `sdk/python/src/matrix_sdk/__init__.py`, `client.py` | União. `MatrixClient.start_run` é o do branch (tracking); o do `main` fica em `client.mlops.start_run`. Mantidos `client.datasets/benchmarks/sandboxes/mlops` e `register_model`. |
| `CLAUDE.md` (auto-merge) | Branch + a seção "Perguntas Interativas" que o `main` acrescentou fora do bloco ATLAS (texto do usuário; bloco ATLAS intacto). |
| `README.md` | Branch + seção "Módulos vindos do `main` (integração em andamento)". |
| ADRs | `main` 006 → `014-mlops-tracking-e-model-registry.md`, 007 → `015-benchmarks-sandboxes-e-data-versioning.md` (títulos e links em `docs/atlas/2026-11-13-*` ajustados). |
| Lockfiles | O `main` não mexeu em `pyproject.toml`/`package.json`; `uv lock --check` ok em server e SDK; `package-lock.json` inalterado. |

`users.is_active`: só o código de auth descartado do `main` lia a coluna; nenhuma migração 0008 foi criada.

## Rotas do `main` e papel

- Só com `current_project_id` no `main` (um viewer escrevia): `POST /datasets`, `GET /datasets`, `GET /datasets/{id}`, `POST /datasets/{id}/items`, `POST /datasets/{id}/items/from-span`, `DELETE /datasets/{id}/items/{item_id}`, `GET /datasets/{id}/export.jsonl`. Agora: leitura VIEWER, escrita MEMBER. (`prompts`, `experiments`, `rules`, `annotations`, `online_evals`, `ingest`, `query` também usavam `current_project_id` no `main`, mas o `main` não os alterou: ficam os do branch, já com `require`.)
- Sem nenhuma autenticação no `main`: `PUT` e `GET /api/v1/mlops/artifacts/local/{token}` (só o token HMAC, assinado com `jwt_secret` público). Agora exigem MEMBER/VIEWER e que a chave do objeto pertença ao projeto do chamador (o SDK já manda o `Authorization` nas URLs internas).
- Inventário (script sobre `app.routes`): 65 rotas dos routers do `main`, todas com `require(...)`; nenhuma de escrita com VIEWER. `tests/test_rbac.py::test_every_mutating_route_declares_rbac` passa sem exceções novas.

## Testes do `main` que falham (não apagados, não marcados skip)

Suíte com Postgres+ClickHouse: rodar com `--continue-on-collection-errors`, porque 4 módulos do `main` não importam (abaixo). Total do `main`: 149 coletados → 131 passam, 10 falham, 8 erros de fixture, + 4 módulos com erro de importação.

| Arquivo | Motivo | Resolve |
|---|---|---|
| `tests/test_auth_rbac.py` (módulo) | importa `create_jwt` / usa JWT, `register`, `project_members` | T3 (reescrever sobre sessões + `memberships`; parte vira obsoleta) |
| `tests/test_benchmarks_api.py` (módulo) | importa `create_jwt` | T3 (e T7) |
| `tests/test_mlops_api.py` (módulo) | importa `create_jwt` | T3 (e T4) |
| `tests/test_sandboxes_api.py` (módulo) | importa `create_jwt` | T3 (e T7) |
| `tests/test_ai_gateway.py` — 5 testes de API (`test_gateway_api_*`) | fixture faz `POST /auth/register` e lê `token` (KeyError) | T3 |
| `tests/test_safety_scanner.py` — 3 testes de API | idem (`register` + `token`) | T3 |
| `tests/test_dataset_versioning.py::test_rbac_and_multitenant_isolation[sqlite/postgres]` | usa `POST /auth/register` (404) | T3 |
| `tests/test_mlops_artifacts.py` — 8 testes | constroem `Settings(jwt_secret=…, artifact_dir=…, artifact_store=…, artifact_presign_ttl_seconds=…)`: campos que deixaram de existir (extra proibido) | T3 (tokens sem `jwt_secret`) / T4 (store sobre `artifacts`) |

Testes do branch alterados: nenhum. Teste do `main` alterado: `sdk/python/tests/test_mlops_sdk.py` usa `client.mlops.start_run(...)` no lugar de `client.start_run(...)` (o nome é do SDK de tracking do branch).
Web: nenhum teste do `main` depende de JWT/cookie por JS (o único código assim era `user-nav.tsx`, sem teste).

## Para T3 / T4

- T3: remover o bloco compat de `auth.py`, `ProjectMember`, `Artifact = MlopsArtifact`, aliases de `config.py`; portar `benchmarks`, `gateway`, `mlops`, `safety`, `sandboxes`, `vectors` e a metade `main` de `datasets` para `Principal`; reescrever os testes acima; `artifact_token_secret` (chave aleatória por processo hoje quebra multi-worker); `mlops._require_admin` usa `ctx.is_admin` (com chave de API nunca é admin — mais restrito que no `main`).
- T4: `mlops_*` sobre `runs`/`run_metrics`/`artifacts`; `mlops_artifact_store` (diretório `artifact_local_dir/mlops`) some; `training_metric_store` vira `metric_store`.
- Tabelas do `main` (`safety_scans`, `mlops_*`, `benchmark_*`, `sandbox_*`) têm `project_id` sem FK para `projects`: o purge de projeto do branch não as apaga (órfãos) — T3/T4.
- `evals/providers.py` do `main` (auto-merge) muda `NO_PROXY` do processo em cada chamada (`_clean_no_proxy`): rever em T8/T12.
- Sandbox `local_sandbox` (RCE no host) e demais riscos da análise §3 seguem no código, só atrás de `require(MEMBER)`: T7/T8. Nada disso deve ir a produção antes do Gate 2.
