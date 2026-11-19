# Reconciliação `main` × branch `ccr-9443eae6-fk5867` — análise (só leitura)

Data: 2026-11-17 · Base comum: `847221d` · `main` = `4d05086` (21 commits) · branch = `a2a7a82` (30+ commits) · Nada foi integrado.

```
ATLAS · audit · arquitetura+dados+infra · large → pesquisa, lane auditoria (comparação por área), relatório · lanes: arquitetura, auditoria
```

Método: o `main` e o branch foram extraídos para pastas de rascunho e três agentes (sonnet/medium, C3) compararam as áreas A (identidade e endurecimento), B (treino, registry, dados) e C (módulos de amplitude e infraestrutura). Só o item "528 passed / 145 skipped" do `main` foi executado (SQLite, sem bancos); o resto é leitura de código. O que não foi verificado está marcado.

## 1. Resumo

- Os dois lados implementaram partes do **mesmo blueprint em paralelo**. O `main` é mais **largo** (sweeps, registry, datasets com branch/merge, linhagem, vetores, red-team, gateway, sandboxes, benchmarks). O branch é mais **profundo e endurecido** (login/RBAC, auditoria, egress, artifact store, runs/séries, 1140 testes com bancos, 3 revisões de segurança, CI com e2e).
- O branch **não** descende do `main`: duas cadeias Alembic (0003/0004 diferentes), tabela `users` definida nos dois, ADRs 006/007 com conteúdo diferente, 22 arquivos de código tocados dos dois lados.
- Há **riscos de segurança concretos no `main`** (seção 3) que não devem entrar como estão.
- Os motores de benchmark e sandbox do `main` são **simulações**, não o Inspect AI / lm-eval / VLMEvalKit reais que foram decididos para este ciclo (seção 4).

## 2. Recomendação por área

| Área | Fica | Por quê (evidência resumida) |
|---|---|---|
| Usuários, sessões, login | **Branch** | `main`: JWT HS256 caseiro de 72 h sem revogação, PBKDF2 100 mil iterações, `POST /auth/register` aberto, sem limite de tentativas. Branch: sessões opacas revogáveis, argon2id, bloqueio escalonado |
| RBAC e proteção de rotas | **Branch** | `main`: `require_role` só nos módulos novos; em `prompts`, `experiments`, `rules`, `annotations`, `online_evals`, `ingest` e `query` os POST/DELETE usam só `current_project_id`, então **um viewer escreve e apaga** |
| Cookies, CSRF, proxy web | **Branch** | `main`: cookie gravado por JavaScript sem `HttpOnly`/`Secure` (`login/page.tsx:40`), proxy sem checagem de Origin, e usa a `MATRIX_API_KEY` global como fallback de identidade |
| Auditoria (`audit_log`) | **Branch** | não existe no `main` |
| Egress / SSRF | **Branch** (e fazer o `main` usar) | `main` tem cópias locais de validação com DNS resolvido duas vezes (rebinding) em `benchmarks/runner.py` |
| Artifact store | **Combinar** | branch: dedupe, quota, locks, hash verificado. `main`: `mlops_artifacts` com versão/linhagem de checkpoint. Proposta: `artifacts` do branch como camada de blobs e `mlops_artifacts` como metadado apontando para `artifacts.id` (decisão de ADR) |
| Config, compose, Dockerfile, CI | **Branch** | `main`: senhas fixas, MinIO com `minioadmin`, portas em todas as interfaces, `uv:latest` como root, `jwt_secret` com padrão público, CI sem guarda de skips/e2e/deploy |
| Tracking de runs e séries | **Branch** | `main`: `MergeTree` sem idempotência (reenvio duplica pontos), sem reaper, sem imutabilidade de params; downsampling por média/min/max perde o primeiro/último ponto. Branch: `ReplacingMergeTree`, M4, reaper, params imutáveis |
| SDK de tracking, telemetria, integrações, ML clássico | **Branch** | `main` só tem HF Trainer e GPU 0; relatórios de ML sem validação no servidor |
| **Sweeps** | **`main`** (portar) | só o `main` tem (grid/random/bayes, ASHA, importância, 658 linhas + testes). Reaproveitar o motor puro sobre `runs`/`run_metrics` e alinhar ao contrato g2 |
| **Registry de modelos/LoRA** | **`main`** (evoluir) | único existente. Faltam: histórico de estágios, `production` único por modelo, webhooks assinados via `egress_client` |
| **Versionamento de datasets** | **`main`** (motor) + combinar | branches, merge de 3 vias, revert, diff; o contrato g3 `/data-sets` (arquivos) fica para depois sobre o artifact store. Colisão direta em `/api/v1/datasets*` |
| **Linhagem** | **`main`** (limitar) | BFS sobre todas as arestas do projeto, sem limite de nós nem profundidade |
| Vetores e drift | **`main`** (com ressalva) | Python puro, embedding por hash de 3-gramas, **sem UMAP/HDBSCAN/scipy**; drift (PSI/JSD/centroide) testado (21 testes), projeção sem teste |
| Red-team / safety | **`main`** (com correção) | heurística própria declarada; **sem alvo real passa tudo com score 100** (falso positivo) |
| Gateway | **`main`** (com correção) | sem chaves virtuais, rate limit só em memória por processo, `mock_fallback_on_no_key=True` devolve resposta falsa em silêncio, SSE não verificado |
| Sandboxes | **`main` só após correção crítica** | ver seção 3 |
| Benchmarks | **`main` só após correção** | `local_sim` inventa nota; `api_key` em claro e devolvida pela API; DNS TOCTOU |

## 3. Riscos de segurança do `main` (devem ser corrigidos antes de qualquer integração)

1. **CRÍTICO — RCE no host:** `runner_backend` padrão `local_sandbox` executa `bash -c`/`python -c` no servidor com `os.environ` completo (JWT secret, chaves de API, URLs de banco). Qualquer **member** pode usar.
2. **CRÍTICO — isolamento some em silêncio:** `DockerSandbox` cai para `LocalSandbox` se o daemon não responde (só um `log.warning`).
3. Sandbox Docker sem `--memory`, `--cpus`, nome do container e kill no timeout (o `subprocess.run` mata só o cliente); `-v` aceita qualquer caminho do host (bloqueia apenas a substring `docker.sock`); `is_admin` lido do `sandbox_policy` enviado pelo cliente.
4. `jwt_secret` com padrão público `"matrix-insecure-dev-secret-change-in-prod"` — também assina os tokens de upload/download de artefatos: forjáveis.
5. Escrita por viewer em `prompts`, `experiments`, `rules`, `annotations`, `online_evals` (ver tabela).
6. `runner_config.api_key` dos benchmarks fica em claro e a API a devolve (`api/benchmarks.py:~116`).
7. Registro aberto, sem rate limit; cookies de sessão legíveis por JavaScript; fallback de identidade pela chave global no proxy web.
8. Resultados simulados sem marcação: `local_sim` grava nota inventada, o gateway responde mock sem chave, safety dá 100 sem alvo.

## 4. Conflito com decisão anterior

Na aprovação do ciclo 2 ficou decidido **usar Inspect AI, lm-eval-harness e VLMEvalKit reais** num runner isolado (ADR-010). O `main` implementa sandboxes e benchmarks **sem** essas bibliotecas (`local_sim`, `LocalSandbox`/`DockerSandbox` próprios) e vetores **sem** UMAP/HDBSCAN (ADR-009). Se os módulos do `main` entrarem, ou a decisão muda para "simulação rotulada primeiro, motores reais depois", ou eles entram atrás de uma interface que aceita o motor real depois.

## 4.1 Colisões técnicas

| Item | `main` | branch | Solução proposta |
|---|---|---|---|
| Alembic | 0003 `users_and_members`, 0004 `safety_scans`, 0005 `mlops_studio`, 0006 `sota_completion` | 0003 `g0_g1…`, 0004 `login_known_ips` | cadeia única: 0001, 0002, **0003/0004 do branch**, 0005 `safety_scans`, 0006 `mlops_studio` (reduzida se rebaseada sobre `runs`), 0007 `sota_completion`; descartar a 0003 do `main` |
| Tabela `users` | email 255, `is_active` | email 320, `last_login_at`, `password_changed_at` | usar a do branch; `is_active` entra como coluna nova se necessário |
| Membros | `project_members` | `memberships` | `memberships`; migrar `ProjectMember`/`create_jwt` do `main` |
| Dependência FastAPI | `AuthContext` + `require_role("viewer")` | `Principal` + `require(Role.VIEWER)` | portar ~12 routers do `main` (mecânico) |
| Runs/treino | `mlops_training_runs`, `/mlops/runs`, ClickHouse `training_metrics` | `runs`, `/runs`, ClickHouse `run_metrics` | pilha única do branch; trocar FKs do registry/sweeps para `runs` |
| Rotas `/api/v1/datasets*` | CRUD + branches/commits/diff/merge | CRUD do ciclo 1 | mesclar no router do `main` com `require(...)` |
| Config | `jwt_secret`, `artifact_store`, `artifact_dir`, `s3_*` | `artifact_backend`, `artifact_local_dir`, `s3_*`, trusted proxies | um só conjunto, sem `jwt_secret` |
| ADRs | 006 mlops, 007 benchmarks/versionamento | 006 RBAC, 007 artifact store, 008–012 | manter 001–012 do branch; renumerar os do `main` para **013** e **014** |
| Estados e campos de run | `completed/cancelled`, `hyperparams`, `hardware_info` | `finished/killed`, `params`, `sys/*` | adotar os do branch; adaptar SDK e web do `main` |

## 5. Ordem sugerida de integração (se aprovada)

1. ADR de reconciliação (quem vence por área, esta tabela) — decisão do usuário.
2. Trazer a infraestrutura do branch como base (CI com e2e/skips/deploy, compose e Dockerfile endurecidos, scripts).
3. Cadeia Alembic única, provada em Postgres limpo (`upgrade`, `check`, `downgrade`).
4. Portar auth do `main` para o modelo do branch; corrigir as rotas sem papel; reconciliar `app.py`, `config.py`, `limits.py`.
5. Trazer `vectors` e `safety` (independentes), com o rótulo `simulated` onde couber.
6. Corrigir e trazer `benchmarks` (mascarar segredo, fixar IP validado, rótulo `simulated`) e `gateway` (sem mock silencioso, rate limit compartilhado).
7. Trazer `sandboxes` **somente** com Docker obrigatório (falhar fechado), limites de memória/CPU, mounts por allowlist e egress do branch.
8. Registry, sweeps, datasets e linhagem sobre `runs`/`artifacts` do branch.
9. Reconciliar web (`api.ts`, `layout.tsx`, login, proxy) e SDK; `tsc`, `vitest`, `build`.
10. Suíte completa com Postgres e ClickHouse, e2e e revisão de segurança antes de qualquer merge.

Esforço: é um ciclo `large` próprio (≈ 8 a 12 tarefas C3–C5), maior que o fechamento do G0+G1.

## 6. Não verificado

Comportamento real dos dois lados em ClickHouse e Postgres; Docker real (flags lidas, não executadas); streaming do gateway; páginas web e testes de web/SDK do `main`; 20 dos 145 testes pulados do `main` sem motivo registrado; `alembic upgrade head` na cadeia unificada; cabeçalhos de download do artifact store do branch.
