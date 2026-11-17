# Ledger — MATRIX SOTA Completion

## Classificação e Decisões (Pré-Gate 1)

- Linha ATLAS: `ATLAS · feature · arquitetura+dados+design+produto · large → pesquisa, descoberta, plano (+ADR), G1, SDD, revisão (+adversarial+segurança), verificação, G2, aprendizado · lanes: design, arquitetura`
- Escopo selecionado pelo usuário via checkbox/multicheck em 2026-11-13:
  1. Benchmarks Fundacionais & Multimodais (LM-Eval / Lighteval / VLMEvalKit)
  2. Sandboxes de Agentes Autônomos (Inspect AI: `Dataset → Solver → Scorer` + Inspect View)
  3. Data Versioning estilo LakeFS/DVC (`branch`, `commit`, `diff`, `merge`, `revert`)
  4. Multimodal Inspector estilo W&B Tables (imagens, bounding boxes, áudio, PDF, text diff em `/datasets` e `/traces`)
  - Runners plugáveis: real (`Docker`/`vLLM`/`HTTP`) + determinístico local para dev/CI.
  - Navegação: rotas dedicadas `/benchmarks` e `/sandboxes` na sidebar + extensões em `/datasets` e `/traces`.

## Rulings

- Ruling (Pré-G1): Hexagonal Ports & Adapters sobre o core relacional + `Job` queue + `ArtifactStore` CAS — evita exigir serviços externos pesados (LakeFS server, Ray, Docker daemon) em dev/CI mantendo 100% de paridade de contrato em produção.
- Ruling (Pré-G1): Proibido `loading.tsx` em `/benchmarks` e `/sandboxes` (restrição Next 15.5.29), proibido `text-zinc-500` em textos `<= 14px` (usar `text-zinc-400`+ para WCAG 2.2 AA) e proibido `bg-emerald-600 text-white` (usar `PRIMARY_BTN_CLASS`).

## Despachos e Execução

- **Gate 1**: Aprovado pelo usuário em 2026-11-13 via pergunta interativa com seleção múltipla.
- **T01** (`[arch] · C5`): Implementado por `arch-implementer`. Modelos SQLAlchemy (`DatasetBranch`, `DatasetCommit`, `DatasetCommitItem`, `BenchmarkRun`, `BenchmarkTaskResult`, `SandboxRun`, `SandboxTaskTranscript`), coluna `DatasetItem.media`, extensão `Media` em `domain/spans.py`, migração `20261114_0006_sota_completion.py`. Validado: `alembic upgrade head`, `downgrade -1`, `upgrade head`, `alembic check` ("No new upgrade operations detected."), 8 testes de storage passaram.
- **T02** (`[arch] · C5`): Implementado por `arch-implementer`. Motor zero-copy `datasets/versioning.py` (canonical hashing determinístico, safe branch name, create_branch, commit_changes, diff_commits, merge_branches 3-way com LCA e detecção de conflito, revert_branch) e API `/api/v1/datasets/{id}/*` com RBAC e tenant isolation. Validado: 15 testes passaram.
- **T03** (`[arch] · C5`): Implementado por `arch-implementer`. Motor plugável `benchmarks/runner.py` (catálogo LM-Eval & VLMEvalKit, anti-SSRF validator, `local_sim` determinístico, `http`/`vllm` adapters, rastreabilidade de linhagem em `mlops_artifact_lineage`), worker handlers `"benchmark.run"` e `"mlops.evaluate"`, router FastAPI `/api/v1/benchmarks/*`. Validado: 22 testes passaram.
- **T04** (`[arch] · C5`): Implementado por `arch-implementer`. Motor Inspect AI `sandboxes/runner.py` (`Dataset → Solver → Scorer`, `local_sandbox` com tempdir isolado, `docker` hardened sem `docker.sock`, `--network=none`, `--read-only`, non-root `65534:65534`), worker handler `"sandbox.run"`, router FastAPI `/api/v1/sandboxes/*`. Validado: 14 testes passaram.
- **T05** (`[core] · C4`): Implementado por `sdd-implementer`. SDK Python `BenchmarksClient`, `SandboxesClient`, extensão de `DatasetsClient` (zero-copy branching, diff, merge, revert, media). Validado: 40 testes no SDK passaram, `ruff check .` passou.
- **T06** (`[core] · C3`): Implementado por `sdd-implementer`. Web Core: tipos em `types.ts`, tokens em `mlops-tokens.ts`, mocks completos em `sota-mocks.ts` e `mock-api.ts`, navegação atualizada em `layout.tsx` com `/benchmarks` e `/sandboxes`. Validado: 195 testes no web passaram, `tsc --noEmit` 0 erros.
- **T07** (`[design] · C4`): Implementado por `design-implementer`. Rota `/benchmarks` (`page.tsx`, `error.tsx`, `components/benchmarks/*`): 4 KPIs, abas LM-Eval & VLMEvalKit, radar SVG acessível + grouped bar + data table toggle, breakdown drawer (log-likelihood vs gen + SymPy), launch modal. Validado: 14 testes de benchmark passaram.
- **T08** (`[design] · C4`): Implementado por `design-implementer`. Rota `/sandboxes` (`page.tsx`, `error.tsx`, `components/sandboxes/*`): 4 KPIs, tabela de runs, launcher modal modular `Dataset → Solver → Scorer`, Inspect View split panel com linha do tempo de passos, exit codes, stdout/stderr, diff de arquivos modificados e veredicto do scorer. Validado: 9 testes de sandbox passaram.
- **T09** (`[design] · C4`): Implementado por `design-implementer`. Rota `/datasets/[datasetId]` estendida com seletor de branch, commit history timeline, diff & merge drawer, tabela multimodal (badges de imagem com bbox, áudio, PDF, text diff), inspector modal drawer e lightbox multimodal em `/traces`. Validado: 15 testes de dataset/multimodal passaram; suite vitest completa com 233 testes em 21 arquivos passou.
- **T10** (`[core] · C4`): Verificação E2E e auditoria de segurança completadas:
  - `make lint` em `server/`, `sdk/python/`, `apps/web/` passou sem erros.
  - `cd apps/web && npm run build` compilou com sucesso (0 warnings de tipo, todas as novas rotas estáticas/dinâmicas geradas).
  - `cd server && uv run pytest -q`: 53 testes SOTA passaram.
  - `cd sdk/python && uv run pytest -q`: 40 testes passaram.
  - `cd apps/web && npx vitest run`: 233 testes passaram (21 arquivos).
  - Capturas de tela realizadas e inspecionadas:
    - `screenshots/benchmarks.png` (Radar visualizer, KPIs, abas LM-Eval e VLMEvalKit)
    - `screenshots/sandboxes.png` (Inspect AI split view, terminal stdout/stderr, timeline de passos)
    - `screenshots/datasets_branching.png` (Branch selector, commit chip, multimodal table cells)
  - Auditoria de segurança aprovada: Docker sandbox sem socket e com rede isolada, SSRF bloqueado em benchmarks, RBAC e validação canônica em versionamento de datasets.

## Status Atual
- Todas as tarefas (T01 a T10) concluídas com 100% de sucesso.
- Pronto para **Gate 2** (confirmação final do usuário para commit e push).

