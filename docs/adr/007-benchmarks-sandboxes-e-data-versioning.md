# ADR-007: Pluggable Benchmarks, Inspect AI Sandboxes, Zero-Copy Data Versioning e Multimodal Tables

- **Status:** Proposed
- **Data:** 2026-11-14
- **Contexto:** Conclusão do Blueprint SOTA (`docs/blueprint-sota.md` §§ 1.1, 1.3, 3.4, 4.1, 4.2) sobre a fundação já existente do MATRIX (`mlops_studio` v0005, `Dataset`/`DatasetItem`, `ArtifactStore` CAS, `Job` queue ADR-003, `Span` domain model e RBAC em `auth.py`).

---

## 1. Contexto e Problema

O MATRIX já possui:
- Tracking de treino, Sweeps, Artifacts CAS (S3/MinIO e LocalFS) e Model/LoRA Registry (`0005_mlops_studio.py`).
- Datasets e Experimentos (`datasets.py`, `experiments.py`).
- Fila de jobs transacional (`storage/jobs.py`, ADR-003) e RBAC (`viewer`, `member`, `admin` em `auth.py`).

Para completar a paridade SOTA descrita em `docs/blueprint-sota.md`, faltam quatro capacidades acopladas ao ciclo de avaliação e dados:
1. **Benchmarks Fundacionais e Multimodais** (LM-Eval & VLMEvalKit) executáveis tanto em clusters GPU (`vllm`/`http`) quanto em CI/dev (`local_sim`).
2. **Avaliação de Agentes em Sandbox Isolado** (modelo Inspect AI `Dataset -> Solver -> Scorer`) com transcrição passo-a-passo e verificação criptográfica de artefatos, sem risco de escape de container ou bloqueio de CI sem Docker daemon.
3. **Versionamento Zero-Copy de Datasets** (estilo DVC/LakeFS: `branch`, `commit`, `diff`, `merge`, `revert`) integrado ao `ArtifactStore` existente.
4. **Tabelas Multimodais** (estilo W&B Tables) suportando `image`, `audio`, `pdf`, `bbox`, `mask` e `text_diff` tanto em `DatasetItem` quanto em `Span`.

---

## 2. Alternativas Arquiteturais Consideradas

### Alternativa A: Orquestração via Serviços Externos Dedicados (LakeFS + Ray + Docker-out-of-Docker)
- **Descrição:** Exigir um servidor LakeFS para branching de dados, cluster Ray para distribuir LM-Eval/VLMEvalKit e daemon Docker obrigatório para Inspect AI.
- **Prós:** Reaproveita binários upstream com mínimo código de controle no `matrix-server`.
- **Contras:** Quebra a portabilidade SQLite + LocalFS do MATRIX; torna testes de integração e CI lentos/frágeis; fragmenta o RBAC por `project_id` e a linhagem (`mlops_artifact_lineage`) entre múltiplos bancos.

### Alternativa B: Hexagonal Ports & Adapters sobre o Core Relacional + Job Queue + CAS do MATRIX (ESCOLHIDA)
- **Descrição:** Definir portas de domínio (`BenchmarkRunner`, `SandboxRunner`, `DatasetVersionStore`) no `matrix-server` com adapters plugáveis:
  - `BenchmarkRunner`: `vllm`, `http` (OpenAI-compatible), `local_sim` (determinístico para CI/dev).
  - `SandboxRunner`: `docker` (container efêmero endurecido, sem socket mount) e `local_sandbox` (sandbox de diretório temporário determinístico para CI/dev).
  - `DatasetVersionStore`: branching zero-copy puramente relacional (`DatasetBranch`, `DatasetCommit`, `DatasetCommitItem`) referenciando blobs por `sha256` no `ArtifactStore`.
- **Prós:**
  - Portabilidade total: SQLite + `local_sim` + `local_sandbox` + `LocalFSArtifactStore` em dev/CI; PostgreSQL + `vllm`/`http` + `docker` + S3/MinIO em produção.
  - Reutiliza `Job` queue (ADR-003), `ArtifactLineage` e `auth.py` RBAC sem novos serviços obrigatórios.
  - Operações `branch` O(1) sem duplicação física de blobs ou linhas.
- **Contras:** Exige implementação dos adapters e do motor de 3-way merge no `matrix-server`.

### Alternativa C: Proxy Pass-Through / Sidecar Assíncrono
- **Descrição:** Apenas armazenar ponteiros para execuções externas disparadas pelo cliente via SDK.
- **Contras:** Não garante reprodutibilidade, não aplica políticas de segurança (anti-SSRF, limites de sandbox) no servidor e não permite reexecução/auditoria centralizada.

---

## 3. Decisões Arquiteturais

### 3.1. Pluggable Benchmark Runner (LM-Eval & VLMEvalKit)
- **Port `BenchmarkRunner`:** recebe `BenchmarkSpec(suite, tasks, eval_mode, num_fewshot, runner_config)` e retorna `list[BenchmarkTaskResult]`.
- **Adapters:**
  - `local_sim`: avaliador determinístico baseado em hash/seed para testes de contrato e CI (suporta `loglikelihood` simulando logprobs e `generate_until` com métricas exatas).
  - `http`: cliente HTTP assíncrono para endpoints OpenAI-compatible (`/v1/completions` com `logprobs`/`echo` para `loglikelihood`, `/v1/chat/completions` para `generate_until` e VLMs), protegido por validação anti-SSRF.
  - `vllm`: adapter de alta vazão para batching e extração direta de logprobs em servidores vLLM.
- **Catálogo SOTA suportado:**
  - `lm_eval`: `mmlu_pro`, `gpqa_diamond`, `ifeval`, `bbh`, `humaneval`, `gsm8k`.
  - `vlmevalkit`: `mmmu_pro`, `mathvista`, `docvqa`, `chartqa`, `ocrbench`, `pope`, `vidore`.
- **Execução:** assíncrona via `Job(kind="benchmark_run")` com lease/heartbeat e atualização de arestas em `mlops_artifact_lineage` (`model_version|dataset -> benchmark_run`).

### 3.2. Pluggable Inspect AI Sandbox Runner
- **Pipeline modular:** `Dataset (task_key, prompt, initial_files, env) -> Solver (react | plan_and_solve | custom) -> Scorer (unit_test | file_exists | regex | artifact_hash | llm_judge)`.
- **Adapters `SandboxRunner`:**
  - `local_sandbox` (dev/CI): cria um workspace isolado via `tempfile.TemporaryDirectory`, sem rede, com allowlist de comandos/interpretador e timeout estrito por comando e por tarefa.
  - `docker` (produção): container efêmero por tarefa com `--network=none` por padrão, `--read-only`, `--user=65534:65534`, `--cap-drop=ALL`, `--security-opt=no-new-privileges:true`, `--pids-limit=128`, e **proibição absoluta** de montar `/var/run/docker.sock`.
- **Transcrição e Verificação:** cada passo do agente é gravado em `SandboxTaskTranscript.steps` (thought, tool_name, tool_args, stdout/stderr truncados, exit_code, tokens, duration_ms) e os arquivos finais verificados têm `sha256` e `size_bytes` conferidos contra `ArtifactStore`.

### 3.3. Zero-Copy Data Versioning
- **Modelo Git-like / LakeFS sobre `Dataset` + `ArtifactStore`:**
  - `DatasetBranch`: ponteiro mutável `(dataset_id, name) -> head_commit_id`. Criação de branch é O(1) e não copia `DatasetItem` nem blobs.
  - `DatasetCommit`: snapshot imutável com `parent_commit_id`, `second_parent_commit_id` (para merges), `tree_hash` e `stats`.
  - `DatasetCommitItem`: associa `(commit_id, item_key)` a `content_hash` (SHA-256 canônico do item) e `artifact_refs` apontando para blobs CAS em `ArtifactStore`.
- **Operações:** `branch`, `commit`, `diff(base, head)`, `merge(source, target, strategy)` (fast-forward + 3-way com LCA e resolução de conflitos `fail`/`ours`/`theirs`), `revert(branch, target_commit_id)`.

### 3.4. Multimodal Table Schema
- Extensão do schema `Media` em `Span` (`domain/spans.py`) e adição da coluna `media: JSON` em `DatasetItem` e `DatasetCommitItem` suportando `image`, `audio`, `pdf`, `bbox`, `mask`, `text_diff` e `file`. Retrocompatibilidade total garantida.

---

## 4. Consequências

- **Positivas:** Arquitetura 100% testável em CI sem GPU ou Docker daemon; versionamento determinístico e auditável; linhagem ponta-a-ponta (`DatasetCommit -> TrainingRun -> ModelVersion -> BenchmarkRun / SandboxRun`).
- **Negativas / Trade-offs:** `DatasetCommitItem` armazena linhas por commit em SQL; para datasets com milhões de linhas no futuro, poderá ser particionado ou movido para manifestos Parquet no `ArtifactStore` mantendo exatamente a mesma API REST.
