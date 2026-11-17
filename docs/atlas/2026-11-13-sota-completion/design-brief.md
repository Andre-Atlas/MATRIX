# Design Brief — MATRIX SOTA Completion (`/benchmarks`, `/sandboxes`, `/datasets` & `/traces` Multimodal)

**Autor:** Design Director (`atlas-lane-design`) · **Data:** 2026-11-13  
**Referências:** `docs/blueprint-sota.md` (Seções 1.1, 1.3, 3.4, 4) · `docs/atlas/2026-11-13-mlops-studio/design-brief.md`

---

## 1. Direção Visual & Auditoria de Tokens ("Precision Dark Calm Telemetry")

- **Superfícies & Tipografia:** Base `#09090b` (`bg-zinc-950`), cards `bg-zinc-900/60` com borda `border-zinc-800`, valores numéricos/hashes/exit codes em `font-mono tabular-nums` (`Mono`), foco `:focus-visible` em `#34d399` (`emerald-400`).
- **Reuso estrito de `apps/web/src/lib/mlops-tokens.ts`:**
  - `PRIMARY_BTN_CLASS`: `border border-emerald-700/70 bg-emerald-900/40 text-emerald-200 hover:bg-emerald-900/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400`
  - `VARIANT_COLORS`: `#38bdf8`, `#a78bfa`, `#fb923c`, `#f472b6`, `#22d3ee`, `#facc15` (até 6 séries/modelos comparados).
  - `RUN_STATUS_STYLE` (`running`, `completed`, `failed`, `stopped_early`, `queued`) com dot `animate-pulse motion-reduce:animate-none` apenas em `running`.
- **Novos tokens semânticos recomendados (estender `mlops-tokens.ts`):**
  - **Sandbox Step Kind (`bash`, `python`, `file_edit`, `tool_call`, `scorer`):**
    - `bash`: `border-amber-800/70 bg-amber-950/40 text-amber-300`
    - `python`: `border-sky-800/70 bg-sky-950/40 text-sky-300`
    - `file_edit`: `border-cyan-800/70 bg-cyan-950/40 text-cyan-300`
    - `tool_call`: `border-orange-800/70 bg-orange-950/40 text-orange-300`
    - `scorer`: `border-violet-800/70 bg-violet-950/40 text-violet-300`
  - **Branch Diff Change Type (`added`, `removed`, `modified`):**
    - `added`: `border-emerald-800/60 bg-emerald-950/40 text-emerald-300` (prefixo `+`)
    - `removed`: `border-red-800/70 bg-red-950/50 text-red-300` (prefixo `-`)
    - `modified`: `border-amber-800/70 bg-amber-950/40 text-amber-300` (prefixo `~`)
- **Regras WCAG 2.2 AA obrigatórias (auditoria vs legado em `ui.tsx`):**
  1. **Proibido `text-zinc-500` em textos/labels `<= 14px`** (ex.: `Kpi`, `th`, `PageHeader` subtitle e `traces/page.tsx` usam `text-zinc-500` no legado; todos os novos componentes e extensões DEVEM usar no mínimo `text-zinc-400` `#a1a1aa` para garantir razão de contraste >= 4.5:1 sobre `#09090b`/`#18181b`).
  2. **Proibido `bg-emerald-600 text-white`** em botões — usar sempre `PRIMARY_BTN_CLASS`.
  3. **Proibido criar arquivos `loading.tsx` de rota** (restrição Next.js 15.5.29) — usar skeletons inline com `role="status"` e `motion-reduce:animate-none`.
  4. **`motion-reduce:animate-none`** obrigatório em todos os indicadores pulsantes e skeletons (`Skeleton`, dots `running`).
  5. **Gráficos SVG puros** (sem bibliotecas pesadas) com `role="img"`, `<title>` e `<desc>` dinâmicos, além de alternador **Chart / Data Table**.

---

## 2. `/benchmarks` — Foundational & Multimodal GPU Benchmarks (LM-Eval & VLMEvalKit)

### 2.1 Header & 4 KPIs
- `PageHeader`: título `"Benchmarks"`, subtítulo (`text-zinc-400`) `"Foundational LLM & Multimodal VLM evaluation suites (LM-Eval / VLMEvalKit)"`, ação primária `+ Launch Benchmark` (`PRIMARY_BTN_CLASS`).
- Grid 4 colunas (`grid-cols-2 lg:grid-cols-4 gap-3`) com labels em `text-zinc-400`:
  1. `Benchmark Runs` (total de execuções + sub: `running` / `completed`).
  2. `Avg MMLU-Pro` (`font-mono tabular-nums`, ex. `68.4%`).
  3. `Avg Multimodal (MMMU/DocVQA)` (`font-mono tabular-nums`, média agregada VLM).
  4. `Active GPU Runners` (ex. `3 / 8 GPUs`, sub com backend `vLLM · SGLang`).

### 2.2 Abas, Matriz Modelos × Benchmarks & Gráficos SVG
- **Abas (`role="tablist"` WAI-ARIA com `ArrowLeft`/`ArrowRight`):**
  1. **LM-Eval (Text & Code)**: MMLU-Pro, GPQA-Diamond, IFEval, BBH, HumanEval, MBPP, TruthfulQA, GSM8K/MATH.
  2. **VLMEvalKit (Multimodal & Visual RAG)**: MMMU-Pro, MathVista, DocVQA, ChartQA, InfoVQA, OCRBench, POPE, HallusionBench, ViDoRe (ColPali/ColQwen2).
- **Barra de Filtros:** `search` (modelo/checkpoint), `eval_mode` (`all` | `loglikelihood` | `generate_until`), `runner` (`vLLM` | `SGLang` | `TensorRT-LLM` | `accelerate`), `status`.
- **Matriz Modelos × Benchmarks:**
  - Coluna de seleção multi-select (até 6 modelos com `VariantSwatch` usando `VARIANT_COLORS`) para comparação visual.
  - Células exibem score principal (`font-mono tabular-nums` + `scoreTone`) e badge discreto do modo (`LL` para `loglikelihood` / `GEN` para `generate_until`).
  - Clicar em uma linha/célula abre o **Breakdown Drawer** com:
    - **Log-likelihood vs Generation Breakdown:** comparação explícita entre avaliação por *logits* (`log P(alternativa | contexto)`, zero erro de parsing, latência baixa) e geração livre (*N-shot*, *chat template*, extração via Regex, verificação simbólica SymPy e taxa de `parse_error %`).
- **Visualização Comparativa SVG Pura (com alternador `Radar | Grouped Bar | Data Table`):**
  - **Radar Chart SVG:** polígonos sobrepostos para até 6 modelos selecionados (`stroke` em `VARIANT_COLORS`, `fillOpacity=0.12`), anéis concêntricos `20%..100%`, eixos rotulados, vértices focáveis por teclado com tooltip de valor exato.
  - **Grouped Bar Chart SVG:** barras agrupadas por benchmark com linha de referência opcional (`baseline`).
  - **Acessibilidade obrigatória:** `<svg role="img" aria-labelledby="...">` com `<title>` e `<desc>` dinâmico listando modelos e faixas de score, além do botão **Chart / Data Table** que renderiza `<table role="grid">` semântica (`<th scope="col|row">`).

### 2.3 Modal `+ Launch Benchmark`
- Focus trap, `Escape` fecha, retorno de foco ao botão disparador.
- Campos: Model / Checkpoint (linkável ao `/models` Registry), Suite (`LM-Eval` | `VLMEvalKit`), Tasks (multi-select com presets), Eval Mode (`loglikelihood` | `generate_until` + `num_fewshot`), Inference Backend (`vLLM` | `SGLang` | `TensorRT-LLM` | `accelerate`), GPU Parallelism (`TP` / `DP`).

### 2.4 Estados (`/benchmarks`)
- **Loading:** 4 KPI skeletons + `TableSkeleton` (`role="status"`, `motion-reduce:animate-none`).
- **Empty:** `EmptyState` com CTA `+ Launch Benchmark`.
- **Error:** alerta `border-red-800/70 bg-red-950/40 text-red-300` com botão `Retry`.
- **Populated / Comparing:** matriz + gráfico SVG sincronizado com os modelos marcados.

---

## 3. `/sandboxes` — Inspect AI Agent Sandboxes & Transcripts

### 3.1 Header & 4 KPIs
- `PageHeader`: título `"Agent Sandboxes"`, subtítulo (`text-zinc-400`) `"Isolated Docker & Kubernetes agent evaluations (Inspect AI)"`, ação primária `+ New Sandbox Run` (`PRIMARY_BTN_CLASS`).
- **4 KPIs** (labels `text-zinc-400`):
  1. `Sandbox Runs` (total + janela 7d).
  2. `Pass Rate` (`%` com `scoreTone` / `Mono`).
  3. `Avg Steps / Task` (`Mono`, ex. `11.4`).
  4. `Active Containers` (ex. `4 Docker · 2 K8s`).

### 3.2 Tabela de Sandbox Runs & Modal `Dataset → Solver → Scorer`
- **Tabela de Runs:** `Run ID`, `Dataset / Task`, `Solver`, `Scorer`, `Runtime` (`Docker` | `K8s`), `Steps`, `Tokens`, `Duration`, `Verdict` (`Pass` / `Fail` / `Running` / `Error`), `Started (UTC)`.
- **Modal `+ New Sandbox Run` (Arquitetura Modular `Dataset → Solver → Scorer`):**
  - Stepper ou 3 seções visuais claras conectadas por `→`:
    1. **Dataset:** seleção da suíte de tarefas, imagem Docker/K8s, limites de CPU/RAM e política de rede (`isolated` / `restricted`).
    2. **Solver:** estratégia (`ReAct`, `Plan-and-Solve`, `Multi-Agent Handoff`), modelo/endpoint, `max_steps`, `token_limit`.
    3. **Scorer:** verificador (`Unit Test Exit 0`, `Filesystem Assertion`, `LLM-as-Judge Rubric`).

### 3.3 Inspect View — Painel Split (Transcrição + Evidências)
- Layout split (`grid-cols-1 lg:grid-cols-12`, lista de passos à esquerda `lg:col-span-5`, inspetor de passo/diff à direita `lg:col-span-7`) com navegação por teclado (`ArrowUp`/`ArrowDown` entre passos):
  - **Timeline de Passos:** cada passo exibe índice (`#01`), badge semântico de tipo (`bash`, `python`, `file_edit`, `tool_call`, `scorer`), duração, tokens e status/exit code (`exit 0` em emerald, `exit != 0` em red com ícone/texto explícito).
  - **Detalhe do Passo:**
    - Comando / argumentos JSON em bloco `<pre className="font-mono text-xs bg-zinc-950 border border-zinc-800 rounded p-3">`.
    - Abas internas (`stdout / stderr` | `Modified Files Diff` | `Scorer Verdict`):
      - `stdout` e `stderr` separados ou com marcador explícito `[stderr]`, toggle `Wrap lines` e botão `Copy`.
      - **Modified Files Diff:** lista de arquivos alterados + diff unificado onde adições têm prefixo `+` + fundo `bg-emerald-950/30 text-emerald-200` e remoções têm prefixo `-` + fundo `bg-red-950/30 text-red-200` (nunca depender apenas de cor).
      - **Scorer Verdict:** explicação do verificador, asserções passadas/falhas e score final.

### 3.4 Estados (`/sandboxes`)
- **Loading:** KPI skeletons + `TableSkeleton` (`role="status"`, `motion-reduce:animate-none`).
- **Empty:** `EmptyState` explicando o fluxo `Dataset → Solver → Scorer` com CTA `+ New Sandbox Run`.
- **Error:** banner de erro com `Retry` (e estado específico se container falhar ao provisionar).
- **Populated / Inspecting:** tabela + painel Inspect View aberto para a run selecionada.

---

## 4. `/datasets` Extensions — LakeFS/DVC Branching & W&B Tables Multimodal Inspector

### 4.1 LakeFS / DVC Zero-Copy Branching (Header & Toolbar de `/datasets/[id]`)
- **Branch Selector:** `<select>` ou combobox acessível no topo do dataset exibindo branch ativa (`main`, `exp/colpali-v2`, `fix/pii-scrub`) + hash do `HEAD` (`Mono`, copiável) + botão `+ New Branch`.
- **Commit History Timeline:** painel/aba lateral ou colapsável listando commits da branch (`hash`, mensagem, autor, contagem de itens `+N / -M / ~K`, timestamp UTC) com ação `Revert` (exige modal de confirmação acessível com impacto explícito).
- **`Diff Branches` Drawer:**
  - Seletores `Base branch` vs `Compare branch`.
  - Contadores resumo (`+ Added`, `- Removed`, `~ Modified`) com símbolos e texto explícito.
  - Lista filtrável de itens alterados com preview de diff e botão `Merge into <base>` (com detecção de conflitos e confirmação).

### 4.2 W&B Tables Multimodal Inspector (Tabela de Itens & Row Drawer)
- Cada linha do dataset suporta células multimodais compactas na tabela e visualização rica no **Row Inspector Drawer**:
  - **Imagem + SVG Bounding Boxes Interativas:**
    - Container `relative inline-block` com `<img>` (`alt` descritivo) e `<svg viewBox="0 0 W H" className="absolute inset-0 h-full w-full">` sobreposto.
    - Caixas `<rect>` com cor por classe + label legível e score de confiança.
    - Toggle `Show/Hide Boxes`, filtro por classe e lista lateral de objetos focável por teclado (focar um item na lista destaca o `<rect>` correspondente no SVG).
  - **Áudio Player:** `<audio controls className="w-full h-9">` + preview opcional de espectrograma/forma de onda em SVG (`role="img"`, `<title>`) + transcrição expansível.
  - **PDF / Documento Visual (ColPali / DocVQA):** mini-paginador (`Page 1 / N`), preview da página renderizada, toggle `Patch Attention Overlay` para RAG visual e aba `Extracted Text`.
  - **Side-by-Side Text Diff:** comparação coluna esquerda (`Reference / Before`) vs direita (`Candidate / After`) com marcação `<ins>`/`<del>` e prefixos `+`/`-`.

---

## 5. `/traces` Multimodal Span Preview (`/traces/[id]`)

- Garantir que payloads multimodais em spans (`LLM`, `RETRIEVER`, `TOOL`, `AGENT`) renderizem inline no inspetor de span sem quebrar a árvore/waterfall:
  - **Imagens & GUI Agent Bounding Boxes:** thumbnails com `max-h-64 object-contain rounded border border-zinc-800` + overlay SVG de clique/bounding box quando houver coordenadas de agente GUI; clique abre modal Lightbox (focus trap, `Escape`, botão `Close` >= 24×24px).
  - **Áudio:** `<audio controls preload="metadata">` compacto com fallback textual se o formato não for suportado.
  - **Páginas de PDF / Visual RAG:** card compacto com número da página, score de recuperação (`ScoreChips`) e thumbnail clicável.
  - **Resiliência:** `loading="lazy"` em mídias e estado de erro limpo caso a URL assinada do MinIO/S3 expire (botão `Reload media` com `text-zinc-400` / `text-red-300`).

---

## 6. Sidebar NAV (`apps/web/src/app/layout.tsx`) & Checklist WCAG 2.2 AA

- **Atualização de `NAV` em `apps/web/src/app/layout.tsx`:**
  Inserir `/benchmarks` e `/sandboxes` logo após `/models`, mantendo a coesão do fluxo MLOps (`Training` → `Models` → `Benchmarks` → `Sandboxes`):
  ```ts
  { href: "/training", label: "Training" },
  { href: "/models", label: "Models" },
  { href: "/benchmarks", label: "Benchmarks" },
  { href: "/sandboxes", label: "Sandboxes" },
  ```
- **Checklist de Conformidade WCAG 2.2 AA (Gate obrigatório de Review):**
  - [ ] Zero `text-zinc-500` em textos informativos/labels `<= 14px` (usar `text-zinc-400` ou superior).
  - [ ] Zero `bg-emerald-600 text-white` (usar `PRIMARY_BTN_CLASS`).
  - [ ] Zero arquivos `loading.tsx` de rota (usar skeletons inline com `role="status"`).
  - [ ] `motion-reduce:animate-none` em toda animação/pulso.
  - [ ] Todo gráfico SVG com `role="img"`, `<title>`, `<desc>` dinâmico e toggle `Chart / Data Table`.
  - [ ] Modais e Drawers com focus trap, `Escape` e alvos clicáveis `>= 24×24px`.
  - [ ] Cor nunca é o único canal de informação (sempre acompanhada de texto, prefixo `+`/`-`/`~` ou padrão visual).
