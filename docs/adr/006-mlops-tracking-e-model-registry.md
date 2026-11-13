# ADR-006: Arquitetura do Módulo MLOps Studio (Tracking de Treino, Sweeps e Model/LoRA Registry)

Status: proposto · 2026-11-13

## Contexto

O MATRIX consolidou o núcleo de LLMOps (ingestão OTLP/OpenInference em ClickHouse, Prompt CMS, Datasets/Experiments, Annotation Queue com export DPO, Auth/RBAC, Vector/Drift Observatory, Safety OWASP e AI Gateway). Conforme [`docs/blueprint-sota.md`](../blueprint-sota.md) (Seções 1 e 7), falta o elo de treinamento e governança de pesos (**Módulo 2: Training & Fine-Tuning Studio** e **Módulo 1: Model/LoRA Registry**), fechando o flywheel de produção (`Traces → Annotation → DPO pairs → Fine-Tuning → Eval Matrix → Model Registry`).

Restrições do sistema:
1. O servidor opera tanto em modo zero-infra para dev/testes (`SQLite` + store em memória) quanto em produção (`PostgreSQL 16` + `ClickHouse 24.8` + `MinIO`, já declarados em `deploy/compose.yaml`).
2. `BodyLimitMiddleware` limita requisições JSON em `/api/v1/*` a 2 MB; checkpoints e adaptadores LoRA `.safetensors` podem atingir dezenas de GB.
3. Treinos e fine-tuning ocorrem em workers GPU externos (PyTorch, TRL, Unsloth, Axolotl, Ray/Slurm), não dentro do container `matrix-worker` atual.
4. Toda leitura e escrita respeita isolamento estrito por `project_id` (ADR-004) e RBAC `viewer`/`member`/`admin`.

## Decisão

Adotamos uma arquitetura poliglota orientada a **Ports & Adapters**:

1. **Séries Temporais de Treino e Telemetria GPU (`TrainingMetricStore`)**:
   - **Produção**: tabela colunar `training_metrics` no **ClickHouse** (`MergeTree`, particionada por `toYYYYMM(recorded_at)`, `ORDER BY (project_id, run_id, category, metric_name, step, recorded_at)`, `CODEC(ZSTD(3))`), com downsampling no servidor (`max_points`) por bucket de `step`.
   - **Desenvolvimento/Testes**: `MemoryTrainingMetricStore` in-process com o mesmo algoritmo de downsampling, espelhando `SpanStore` (ADR-003).
2. **Metadados, Sweeps, Lineage DAG e Model/LoRA Registry**:
   - Persistidos no banco relacional (**PostgreSQL** / **SQLite**) via SQLAlchemy 2 + tipos portáveis e migrações reversíveis no Alembic (`mlops_sweeps`, `mlops_training_runs`, `mlops_artifacts`, `mlops_artifact_lineage`, `mlops_registered_models`, `mlops_model_versions`, `mlops_model_aliases`).
   - Versões de modelo (`ModelVersion`) são imutáveis; aliases (`staging`, `production`, `champion`, `challenger`) são ponteiros mutáveis protegidos por RBAC (`admin` obrigatório para `production`/`champion`).
3. **Armazenamento de Artefatos (`ArtifactStore`)**:
   - **Produção**: **MinIO / S3** via URLs pré-assinadas geradas em Python puro (AWS SigV4 sobre `hmac`/`hashlib`, zero dependência `boto3`) e endereçamento por conteúdo (`sha256`) para deduplicação por projeto, sem trafegar binários pelo FastAPI. Suporta `storage_backend="external"` (`s3://`, `gs://`, `hf://`, `lakefs://` via allowlist anti-SSRF).
   - **Desenvolvimento/Testes**: `LocalFSArtifactStore` usando URLs locais assinadas por HMAC.
4. **Orquestração de Sweeps (Ask-and-Tell + Early Stopping)**:
   - Control plane em Python puro no `matrix-server` (`grid`, `random`, `bayes`), com avaliação incremental de *Early Stopping* (`asha` / `median`) durante `POST /api/v1/mlops/runs/{run_id}/metrics`, retornando `should_stop` ao SDK.

## Alternativas Consideradas

- **Tudo no PostgreSQL + URIs externas (sem ClickHouse/MinIO):** Rejeitada por bloat/lentidão de séries temporais de alta frequência no Postgres e por deixar o `minio` do `deploy/compose.yaml` ocioso.
- **Sidecars MLflow + LakeFS + Ray Tune:** Rejeitada por quebrar o modelo multi-tenant/RBAC unificado do MATRIX, inviabilizar o modo dev zero-infra e triplicar a pegada operacional.

## Consequências

- **Positivas:** Escalabilidade de curvas de treino no ClickHouse; zero gargalo de memória na API para checkpoints grandes; linhagem DAG completa no mesmo banco (`Dataset` / DPO → `TrainingRun` → `Artifact` → `ModelVersion` → `Experiment` & `SafetyScan`); zero novas dependências pesadas no `server/` e no `sdk/`.
- **Negativas:** Manutenção de paridade entre `MemoryTrainingMetricStore` e `ClickHouseTrainingMetricStore` (garantida por testes parametrizados compartilhados).
- **Riscos:** Uploads `pending_upload` abandonados no MinIO (mitigado por cleanup/TTL); SSRF em URIs externas (mitigado por allowlist de esquemas e bloqueio de IPs privados/loopback, mantendo `MATRIX_S3_ENDPOINT` apenas via `Settings`).
