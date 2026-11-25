# T7 — Sandboxes (Docker fail-closed) e Benchmarks (segredos, egress, `simulated`)

Branch `ccr-9443eae6-fk5867`. Base: ADR-010, ADR-012, ADR-013, `tasks/T7-sandboxes-benchmarks.md`.

## Decisões

### Sandboxes
- **Execução no host removida:** `LocalSandbox` e o fallback silencioso `DockerSandbox -> LocalSandbox` foram completamente removidos.
- **`SandboxEngine` (`docker`, `fake`):** interface abstrata em `sandboxes/runner.py` com duas implementações:
  - `DockerSandbox` (padrão, `simulated=False`): falha fechada (`SandboxUnavailableError` -> `status="sandbox_unavailable"`) se o daemon/CLI Docker estiver indisponível ou se a conexão com o daemon falhar. Nunca cai para execução no host.
  - `FakeSandbox` (`simulated=True`): permitido **somente** quando `MATRIX_ENV` está explicitamente em `{"test", "dev"}`; nunca chama `subprocess`/`os.system`/`eval`/`exec`, produzindo saída determinística.
  - Inspect AI real continua como motor alvo no serviço `matrix-runner` (ADR-010), não implementado agora.
- **`build_docker_run_args` único e endurecido:** `--rm --name matrix-sbx-<run>-<sample>-<rand> --init --network none --read-only --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges:true --pids-limit <cfg,128> --memory <cfg,512m> --memory-swap <igual> --cpus <cfg,1,máx 4> --tmpfs /tmp:rw,noexec,nosuid,size=256m --ipc none --ulimit nofile=256:256 --ulimit fsize=67108864:67108864`.
- **Allowlist de imagens do operador:** `MATRIX_SANDBOX_IMAGES` em `config.py` (padrão `python:3.12-slim`); imagem fora da lista -> 422.
- **Sem mounts/volumes do usuário e sem rede:** `mounts`/`volumes` na política -> 422; `network_mode != "none"` -> 422 com `"network access for sandboxes is not supported until an egress proxy exists"` (inclusive para admin; fluxo de aprovação de rede removido); chaves reservadas (`is_admin`, `network_approved_by`) -> 422. O runner cria diretório temporário `0700` por amostra montado apenas em `/workspace` e o remove em `finally`.
- **Ambiente mínimo para o CLI `docker`:** apenas `PATH`, `HOME=""` e `DOCKER_HOST` (se definido); variáveis do servidor não vazam para o CLI nem para o container.
- **Timeout, truncamento e OOM:** timeout executa `docker kill <name>` + `docker rm -f <name>` e limpeza do tmpdir em `finally`; stdout/stderr truncados em 64 KiB (`65536` bytes); exit code `137` -> `status="oom"` no transcript.
- **`sync_exec`:** só executa sincronamente em ambiente de teste (`MATRIX_ENV=test` ou `PYTEST_CURRENT_TEST` quando `MATRIX_ENV` não está definido); em produção fica enfileirado para o worker.
- **`simulated` exposto** em `SandboxRun` e `SandboxTaskTranscript` na API.
- **Skip allowlist:** testes marcados com `@pytest.mark.docker` pulam com o motivo exato `"Docker daemon not available"` (adicionado à allowlist de `scripts/check_skips.py` e coberto em `server/tests/test_check_skips.py`).

### Benchmarks
- **Segredos:** `runner_config.api_key` literal é recusado com 422 orientando usar `api_key_env` (`^[A-Z][A-Z0-9_]{0,63}$`). O valor da variável de ambiente é lido apenas em tempo de execução, nunca persistido no banco, nunca retornado pela API (`sanitize_runner_config` defensivo) e nunca logado.
- **SSRF / Egress:** `validate_runner_url` customizado e `run_http_benchmark_async` (não usado) foram removidos. O motor `http`/`vllm` roteia todas as chamadas exclusivamente pelo `egress_client` do branch (`purpose="benchmark.run"`), garantindo allowlist por projeto, resolução DNS única com IP fixado, revalidação de redirects e auditoria em `audit_log`. Na criação via API há apenas pré-checagem sintática e de literais IP proibidos/privados (sem DNS duplo).
- **`simulated`:** `local_sim` marca `simulated=True` em `BenchmarkRun` e `BenchmarkTaskResult`; `http`/`vllm` marca `simulated=False`. Exposto na API.
- **`BenchmarkEngine`:** interface abstrata com `LocalSimBenchmarkEngine`, `HttpBenchmarkEngine` e stubs `LmEvalBenchmarkEngine`/`VlmEvalBenchmarkEngine` que levantam `EngineUnavailable("install the real engine")`.

### Migração
- Fragmento `server/migrations/fragments/t7_sandboxes_benchmarks.py` (`upgrade()`/`downgrade()`): adiciona colunas `simulated` nas 4 tabelas, amplia `sandbox_runs.status` para `String(24)` e altera default de `runner_backend` para `"docker"`, faz backfill de `simulated=true` para `local_sim`/`fake` e remove qualquer `api_key` em claro de `benchmark_runs.runner_config`. Testado em `server/tests/test_t7_migration_fragment.py`.

## Arquivos tocados
- `server/src/matrix_server/config.py`
- `server/src/matrix_server/sandboxes/runner.py`
- `server/src/matrix_server/sandboxes/__init__.py`
- `server/src/matrix_server/api/sandboxes.py`
- `server/src/matrix_server/benchmarks/runner.py`
- `server/src/matrix_server/benchmarks/__init__.py`
- `server/src/matrix_server/api/benchmarks.py`
- `server/migrations/fragments/t7_sandboxes_benchmarks.py`
- `server/pyproject.toml`
- `scripts/check_skips.py`
- `server/tests/test_sandboxes_api.py`
- `server/tests/test_benchmarks_api.py`
- `server/tests/test_check_skips.py`
- `server/tests/test_t7_migration_fragment.py`
- `server/tests/test_auth_rbac.py`
- `server/tests/test_project_delete_main_modules.py`
- `docs/atlas/2026-11-17-reconciliacao/t7-notes.md`

## Riscos abertos
- Testes `@pytest.mark.docker` exigem daemon Docker ativo (no sandbox da VM sem `dockerd`, pulam com `"Docker daemon not available"` e são cobertos por testes unitários com `subprocess.run` mockado e `DOCKER_HOST` apontando para socket morto). Executar com Docker real na integração/CI.
- Fragmento `t7_sandboxes_benchmarks.py` precisa ser incorporado na revisão Alembic consolidada (0009+) na fase de integração.
- Motores reais (Inspect AI, `lm-evaluation-harness`, `VLMEvalKit`) permanecem como interfaces/stubs `EngineUnavailable` até a entrega do `matrix-runner` (ADR-010).
