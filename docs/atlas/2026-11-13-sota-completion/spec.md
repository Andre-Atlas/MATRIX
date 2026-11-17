# Especificação Técnica: SOTA Completion (Benchmarks, Inspect AI Sandboxes, Zero-Copy Data Versioning & Multimodal Tables)

- **ADR:** `docs/adr/007-benchmarks-sandboxes-e-data-versioning.md`
- **Migration:** `0006_sota_completion.py` (revises `0005`)
- **Compatibilidade:** SQLite 3.x (dev/tests) + PostgreSQL 14+ (produção)

---

## 1. Modelos SQLAlchemy (`server/src/matrix_server/storage/models.py`)

### 1.1. Extensão em `DatasetItem`
- Adicionar `media: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)`.

### 1.2. Zero-Copy Data Versioning
- `DatasetBranch` (`dataset_branches`): `id`, `project_id`, `dataset_id` (FK CASCADE), `name`, `head_commit_id` (nullable, sem FK circular), `is_default`, `created_by`, `created_at`, `updated_at`. Unique `(dataset_id, name)`.
- `DatasetCommit` (`dataset_commits`): `id`, `project_id`, `dataset_id` (FK CASCADE), `parent_commit_id`, `second_parent_commit_id`, `tree_hash`, `message`, `author`, `item_count`, `stats`, `created_at`.
- `DatasetCommitItem` (`dataset_commit_items`): `id`, `project_id`, `commit_id` (FK CASCADE), `item_key`, `content_hash`, `input`, `expected_output`, `context`, `media`, `metadata_`, `artifact_refs`, `created_at`. Unique `(commit_id, item_key)`.

### 1.3. Benchmarks (LM-Eval & VLMEvalKit)
- `BenchmarkRun` (`benchmark_runs`): `id`, `project_id`, `name`, `suite` (`lm_eval | vlmevalkit`), `tasks`, `runner_backend` (`vllm | http | local_sim`), `runner_config`, `eval_mode` (`loglikelihood | generate_until | multimodal`), `num_fewshot`, `target_model`, `model_version_id` (FK `mlops_model_versions.id` SET NULL), `dataset_id` (FK `datasets.id` SET NULL), `dataset_commit_id`, `status` (`queued|running|completed|failed|cancelled`), `summary_metrics`, `job_id`, `error`, `created_at`, `started_at`, `finished_at`.
- `BenchmarkTaskResult` (`benchmark_task_results`): `id`, `project_id`, `benchmark_run_id` (FK CASCADE), `task_name`, `modality`, `eval_mode`, `num_fewshot`, `sample_count`, `primary_metric`, `primary_score`, `metrics`, `latency_ms_p50`, `latency_ms_p95`, `created_at`. Unique `(benchmark_run_id, task_name)`.

### 1.4. Inspect AI Sandboxes
- `SandboxRun` (`sandbox_runs`): `id`, `project_id`, `name`, `runner_backend` (`docker | local_sandbox`), `dataset_id` (FK `datasets.id` SET NULL), `dataset_commit_id`, `solver_config`, `scorer_config`, `sandbox_policy`, `status` (`queued|running|completed|failed|cancelled`), `pass_rate`, `total_tasks`, `passed_tasks`, `total_tokens`, `total_cost_usd`, `job_id`, `error`, `created_at`, `started_at`, `finished_at`.
- `SandboxTaskTranscript` (`sandbox_task_transcripts`): `id`, `project_id`, `sandbox_run_id` (FK CASCADE), `task_key`, `status` (`passed | failed | error | timeout`), `score`, `scorer_details`, `steps`, `verified_artifacts`, `duration_ms`, `input_tokens`, `output_tokens`, `error`, `created_at`. Unique `(sandbox_run_id, task_key)`.

---

## 2. Contratos REST OpenAPI (`/api/v1/...`)

### 2.1. Benchmarks (`/api/v1/benchmarks/*`)
- `GET /api/v1/benchmarks/catalog` (`ViewerAuth`)
- `POST /api/v1/benchmarks/runs` (`MemberAuth`) → `201 BenchmarkRunOut` (enfileira job ou executa inline se configurado `sync_exec=True` para testes)
- `GET /api/v1/benchmarks/runs` (`ViewerAuth`)
- `GET /api/v1/benchmarks/runs/{run_id}` (`ViewerAuth`) → inclui `task_results`
- `POST /api/v1/benchmarks/runs/{run_id}/cancel` (`MemberAuth`)

### 2.2. Sandboxes Inspect AI (`/api/v1/sandboxes/*`)
- `POST /api/v1/sandboxes/runs` (`MemberAuth`; `AdminAuth` se `network_mode != "none"`) → `201 SandboxRunOut`
- `GET /api/v1/sandboxes/runs` (`ViewerAuth`)
- `GET /api/v1/sandboxes/runs/{run_id}` (`ViewerAuth`)
- `GET /api/v1/sandboxes/runs/{run_id}/transcripts` (`ViewerAuth`)
- `GET /api/v1/sandboxes/runs/{run_id}/transcripts/{task_key}` (`ViewerAuth`)
- `POST /api/v1/sandboxes/runs/{run_id}/cancel` (`MemberAuth`)

### 2.3. Zero-Copy Data Versioning (`/api/v1/datasets/{dataset_id}/...`)
- `GET /api/v1/datasets/{dataset_id}/branches` (`ViewerAuth`)
- `POST /api/v1/datasets/{dataset_id}/branches` (`MemberAuth`)
- `GET /api/v1/datasets/{dataset_id}/commits` (`ViewerAuth`)
- `POST /api/v1/datasets/{dataset_id}/commits` (`MemberAuth`)
- `GET /api/v1/datasets/{dataset_id}/commits/{commit_id}/items` (`ViewerAuth`)
- `GET /api/v1/datasets/{dataset_id}/diff` (`ViewerAuth`, query `base`, `head`)
- `POST /api/v1/datasets/{dataset_id}/merge` (`MemberAuth`, retorna `409 Conflict` se conflito e `conflict_strategy=="fail"`)
- `POST /api/v1/datasets/{dataset_id}/revert` (`MemberAuth`)

### 2.4. Multimodal Media Schema (`Span.media` & `DatasetItem.media`)
- `kind`: `"image" | "audio" | "pdf" | "bbox" | "mask" | "text_diff" | "file"`
- Campos: `url`, `artifact_id?`, `sha256?`, `mime`, `width?`, `height?`, `page?`, `duration_ms?`, `boxes?` (`[{x,y,w,h,label,score}]`), `mask_uri?`, `diff?` (`{before,after,hunks}`).

---

## 3. Invariantes de Segurança & RBAC
1. **No Docker Socket Escape:** `docker` runner nunca monta `/var/run/docker.sock`; executa com `--user=65534:65534`, `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges:true`, `--pids-limit=128`, `--network=none` (padrão). `network_mode != "none"` exige `AdminAuth`.
2. **No SSRF:** `runner_config.endpoint_url` e `media[].url` externas bloqueiam IPs privados/loopback/link-local (`127.0.0.0/8`, `10.0.0.0/8`, `169.254.169.254`, `::1`), credenciais embutidas e hostnames `.local`/`.internal`.
3. **No Path Traversal:** `branch_name`, `task_key`, `item_key`, `verified_artifacts[].path` validados estritamente.
4. **Bounded Quotas:** `timeout_seconds` ∈ `[1, 3600]`, `task_timeout_seconds` ∈ `[1, 600]`, `max_steps` ∈ `[1, 100]`, `stdout`/`stderr` truncados em `65536` bytes.
5. **Anti-NaN / Anti-IDOR:** rejeição de `NaN`/`Inf` e filtro obrigatório por `project_id == ctx.project_id`.

---

## 4. Extensões do SDK Python (`matrix_sdk`)
- `matrix_sdk.benchmarks.BenchmarksClient` (`client.benchmarks`)
- `matrix_sdk.sandboxes.SandboxesClient` (`client.sandboxes`)
- `matrix_sdk.datasets.DatasetsClient` (extensão: `media` em `add_item`, `create_branch`, `list_branches`, `commit`, `list_commits`, `diff`, `merge`, `revert`)
