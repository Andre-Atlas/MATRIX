# Ciclo 3 — Tudo local: motores reais, recursos adiados, infra e pendências da auditoria

`ATLAS · feature+fix · arquitetura, auditoria, design · large → spec+plano (G1), execução por tarefas, revisão, auditoria final, G2 · lanes: arquitetura, auditoria, design`

## Contexto
`main` (`82643fe`) tem T1–T13 (PR #1 e #2). Pendências: 23 MEDIUM + 4 LOW de `docs/atlas/2026-11-17-reconciliacao/audit-final.md`, motores reais (hoje stubs `EngineUnavailable` ou simulados rotulados), S3/compose não validados, recursos adiados (chaves virtuais, SSE, `/data-sets` por manifesto) e o commit `ff98b61` (commit da sessão antes dos cabeçalhos HTTP), que ficou fora do `main` e já foi trazido a este branch.

**Restrição do usuário, prioritária:** tudo roda localmente, sem gasto com deploy, nuvem ou API paga. Quando isso colidir com outra escolha, prevalece, desde que a qualidade não caia demais. O propósito continua sendo **unificar os frameworks** numa plataforma só.

## Decisões de arquitetura (ADR-016, a escrever em L1)
1. **Um servidor de modelo local para tudo: Ollama** (serviço `ollama` no compose, perfil `llm`, volume nomeado). Modelos padrão pequenos e para CPU: `qwen2.5:0.5b` (chat/completions), `all-minilm` (embeddings), `moondream` (visão, só para VLMEvalKit). Funciona em Mac (arm64) e Linux, sem GPU. O operador pode apontar outro servidor compatível com OpenAI (llama.cpp, vLLM) pela mesma configuração.
2. **Endpoints de modelo locais confiáveis pelo operador:** a política de egress (ADR-012) bloqueia IP privado, e o Ollama vive em `localhost` ou na rede do compose. Cria-se `MATRIX_LOCAL_MODEL_ENDPOINTS` (lista de URLs fixada pelo operador, nunca vinda do usuário). Só esses hosts podem ser privados, e só para os fins `model`/`embedding`. O resto do egress continua igual, e redirect para fora da lista é bloqueado.
3. **Motores reais, todos locais, sobre esse servidor:**
   - **lm-eval** via `local-chat-completions`/`local-completions` contra o Ollama, com `limit` de amostras para caber em CPU.
   - **VLMEvalKit** via o wrapper de API compatível com OpenAI contra `moondream`, com subconjunto pequeno.
   - **Inspect AI** com sandbox Docker endurecido (as flags da T7 aplicadas ao compose que o Inspect gera) e modelo `openai-api` apontando para o Ollama.
   - **UMAP** (`umap-learn`) + **HDBSCAN** (`sklearn.cluster.HDBSCAN`) com embeddings reais do Ollama.
   - **Safety** com alvo real pelo gateway (modelo local).
   - Resultados desses motores saem com `simulated=false`. `local_sim`, `fake`, hash-3gram e `mock` continuam como modo demo, rotulados.
4. **Dependências pesadas em extras opcionais** (`engines`: lm-eval, inspect-ai, vlmeval, umap-learn). Sem o extra, o motor responde `EngineUnavailable` com mensagem de instalação, nunca resultado falso.
5. **CI sem custo:** os jobs atuais ficam como estão. Os jobs com motores reais e Ollama rodam só por `workflow_dispatch`/label, e a validação de verdade é local (`make verify-local`).

## Tarefas
| # | Tarefa | Lane | Nível | Modelo/esforço |
|---|---|---|---|---|
| L1 | ADR-016 + `MATRIX_LOCAL_MODEL_ENDPOINTS` no egress (fins `model`/`embedding`, IP privado só para hosts do operador, testes de SSRF) + serviço `ollama` no compose (perfil `llm`) + `make llm-pull` | `[arch]` | C4 | opus/medium + segurança |
| L2 | Gateway: provedor `ollama`/`openai_compatible` local, **SSE** (`stream: true`), **chaves virtuais** (ADR-011: `mxv_`, hash argon2/sha256, orçamento e limites por chave, escopo por projeto), MEDIUM do gateway (httpx com pool via `net.py`, estimativa de tokens por caracteres) | `[core]` | C4 | opus/medium + segurança |
| L3 | Benchmarks: motor **lm-eval** real (CPU, `limit`), **VLMEvalKit** real (subconjunto, `moondream`), `simulated=false`, progresso por tarefa, timeouts e cancelamento no worker | `[arch]` | C4 | opus/medium |
| L4 | Sandboxes: motor **Inspect AI** real (sandbox Docker com as flags da T7, rede `none`, modelo local), transcript real; MEDIUM: ReDoS no scorer regex (timeout/RE2-like), `null` em limites, `sandbox_unavailable` preservado | `[arch]` | C4 | opus/medium + segurança |
| L5 | Vetores: embeddings reais (Ollama), **UMAP + HDBSCAN** reais, drift sobre embeddings reais; MEDIUM: N+1 de spans e `O(Q·D)` em knowledge gaps | `[core]` | C3 | sonnet/medium |
| L6 | Safety: alvo real pelo gateway/modelo local (`simulated=false`), scans assíncronos no worker | `[core]` | C3 | sonnet/medium |
| L7 | `/data-sets` por manifesto de arquivos (g3 §2) sobre o artifact store, ligado a runs e linhagem | `[core]` | C3 | sonnet/medium |
| L8 | Pendências MEDIUM/LOW do **servidor**: posse por projeto de `sweep_id`/`dataset_commit_id`, webhook inativo deixa `queued`, paginação de `sweep_detail`/`list_models`/`diff_commits`/`find_lca`, importância de sweep fora do `GET`, `_fetch_hop_edges` | `[core]` | C3 | sonnet/medium |
| L9 | Pendências MEDIUM/LOW da **web + SDK**: erros engolidos, fallbacks sintéticos em `benchmarks.ts`/`vectors.ts`, `normalizeImportance`, waterfalls SSR, linhagem `O(V·E)`, `MLOpsClient`/`Run.finish`/`poll_paused`, `npm audit`; telas novas (chaves virtuais, streaming no playground, motores reais nos selos) | `[design]` | C4 | opus/medium + design-director |
| L10 | Infra local: S3 real com RustFS **sem skips**, `docker compose up` completo (perfis `dev`, `llm`), `make up` / `make verify-local` em um comando, guia "rodar localmente" (Mac e Linux) | `[arch]` | C3 | sonnet/medium |
| L11 | Testes e CI: lacunas T5–T8, `check_skips` exige Docker no CI e roda no job `sdk`, `/sys/class/net` em OrbStack/LinuxKit, Vitest/Playwright das telas novas, jobs pesados só por dispatch | `[core]` | C3 | sonnet/medium |
| L12 | Auditoria final (lane auditoria, `audit-lead`), verificação local completa, relatório, PR (Gate 2) | `[audit]` | C5 | opus/high |

Ordem: L1 → (L2 ∥ L3 ∥ L4 ∥ L5 ∥ L7 ∥ L8) → L6 (depende de L2) → L9 (depende das APIs) → L10 → L11 → L12. Paralelos sempre em módulos disjuntos, 2 a 3 por vez; migrações por fragmento em `server/migrations/fragments/`, montadas em 0010+ por quem coordena.

## Verificação
Suíte do servidor com Postgres + ClickHouse + Docker + RustFS (sem skips de S3), testes de motores reais contra Ollama local (marcador `engines`, pulam só com o motivo exato "Ollama not available" fora do `make verify-local`), SDK, web (`tsc`/`vitest`/`build`), Alembic em banco novo, `docker compose --profile llm up` + `make demo` + Playwright.

## Riscos
- **CPU sem GPU:** benchmarks reais só com subconjuntos e modelos de 0,5B. Os números servem para provar que o motor real roda, não para comparar modelos.
- **Peso:** `inspect-ai`, `lm-eval` e `vlmeval` puxam muitas dependências (torch em CPU no VLMEvalKit), por isso ficam em extra opcional.
- **Rede de modelo privada:** é a exceção ao egress mais sensível. Exige revisão de segurança.
