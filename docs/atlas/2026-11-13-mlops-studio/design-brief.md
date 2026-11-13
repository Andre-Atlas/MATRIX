# Design Brief — MATRIX MLOps Studio (`/training` & `/models`)

**Autor:** Design Director (`atlas-lane-design`) · **Data:** 2026-11-13

## 1. Direção Visual — "Precision Dark Calm Telemetry"
- Base `#09090b` (`bg-zinc-950`), cartões `bg-zinc-900/60` com bordas `border-zinc-800`, tipografia numérica `font-mono tabular-nums` (`Mono`), zero bibliotecas de gráficos pesadas (gráficos em SVG puro com tokens do design system; DAG de linhagem com `@xyflow/react` usando as CSS vars de `globals.css`).
- Acento `emerald-400` (`#34d399`) reservado para ação primária (`primaryBtn`), foco `:focus-visible`, status `completed` / `Production` / `@champion`. Paleta categórica para runs sobrepostas segue `VARIANT_COLORS` (`#38bdf8`, `#a78bfa`, `#fb923c`, `#f472b6`, `#22d3ee`, `#facc15`).
- **Correções A11y obrigatórias em relação ao legado:**
  - Não usar `text-zinc-500` em textos informativos/labels `<= 14px` (usar no mínimo `text-zinc-400` para contraste >= 4.5:1 AA).
  - Não usar `bg-emerald-600 text-white` em botões (usar `primaryBtn`: `border-emerald-700/70 bg-emerald-900/40 text-emerald-200` ou `bg-emerald-500 text-zinc-950`).

## 2. Tokens Semânticos (`apps/web/src/lib/mlops-tokens.ts`)
- **Run Status (`running`, `completed`, `failed`, `stopped_early`/`killed`, `queued`)**:
  - `running`: `border-sky-800/70 bg-sky-950/40 text-sky-300` + dot `bg-sky-400 animate-pulse motion-reduce:animate-none`
  - `completed`: `border-emerald-800/60 bg-emerald-950/40 text-emerald-300` + dot `bg-emerald-400`
  - `failed`: `border-red-800/70 bg-red-950/50 text-red-300` + dot `bg-red-400`
  - `stopped_early`/`killed`: `border-amber-800/70 bg-amber-950/40 text-amber-300` + dot `bg-amber-400`
- **Model Stage (`none`, `staging`, `production`, `archived`)**:
  - `production`: `border-emerald-800/60 bg-emerald-950/40 text-emerald-300`
  - `staging`: `border-amber-800/70 bg-amber-950/40 text-amber-300`
  - `archived`: `border-zinc-700/80 bg-zinc-900 text-zinc-400`
- **Model Aliases (`rounded-full`)**:
  - `@champion`: `border-emerald-700/70 bg-emerald-950/60 text-emerald-300`
  - `@challenger`: `border-violet-700/70 bg-violet-950/60 text-violet-300`
  - Outros aliases: `border-sky-800/70 bg-sky-950/50 text-sky-300`
- **DAG Node Accent**: `Dataset` (`cyan`), `TrainingRun` (`sky`), `Artifact`/`ModelVersion` (`emerald`), `Experiment`/`SafetyScan` (`violet`).

## 3. Telas, Layout e Estados Obrigatórios

### 3.1 `/training` (Training Studio & Sweeps)
- **Header (`PageHeader`) + 4 KPIs**: `Active Runs`, `Completed (7d)`, `Best Eval Loss`, `Avg GPU VRAM`.
- **3 Abas (`role="tablist"` WAI-ARIA com setas `ArrowLeft`/`ArrowRight`)**:
  1. **Runs & Live Curves**: Filtros (`search`, `status`, `run_type`), tabela multi-select (até 6 runs com `VariantSwatch`), gráfico SVG interativo (`Train & Eval Loss`, `Perplexity`, `DPO Reward Margin`, `Learning Rate`) com suavização EMA, toggle `Linear / Log Y`, crosshair + tooltip, mini-gráficos de Telemetria GPU (`Utilization %`, `VRAM GB`, threshold `90%`), botão `Compare Selected (N)` (Drawer de diff de hiperparâmetros e delta de métricas) e ação `Register Model`.
  2. **Hyperparameter Sweeps**: Seletor de Sweep, gráfico SVG de **Coordenadas Paralelas** com brushing interativo por eixo (também controlável via inputs `min`/`max` acessíveis por teclado), tabela de trials filtrada em tempo real e painel de importância de hiperparâmetros.
  3. **Classic ML & Eval**: Seletor de run, curva **ROC-AUC** SVG (com linha random `0.50` e marcador Youden's J), **Matriz de Confusão** (`<table role="grid">` com toggle `Counts` / `Row %`) e barras divergentes **Feature Importance / SHAP**.
- **Estados**: Loading skeletons (`role="status"`), Empty state geral e por aba (ex. SFT sem métricas clássicas), Error alert com Retry, Populated, Modais/Drawers com focus trap e `Escape`.

### 3.2 `/models` (Model & LoRA Registry + Lineage DAG)
- **Header + 4 KPIs**: `Registered Models`, `In Production (@champion)`, `In Staging (@challenger)`, `LoRA Adapters`.
- **Catálogo + Detalhe**:
  - Filtros (`search`, `task_type`, `stage`, `alias`).
  - Tabela de modelos com `Type Badge`, `Latest Version`, `Aliases`, `Stage Badge`, `Metrics`, `Updated (UTC)`.
  - Painel de detalhe com 3 abas (`role="tablist"`):
    1. **Versions & Promotion**: tabela de versões imutáveis com config LoRA (`r`, `alpha`), link para `/training?run=...`, ações `Promote Stage`, `Move Alias` (modal com callout de impacto caso `@champion` mude de versão) e `Compare Versions`.
    2. **Lineage DAG**: grafo interativo com `@xyflow/react` (`Dataset` → `TrainingRun` → `Artifact` → `ModelVersion` → `Experiment`/`SafetyScan`), destaque de caminho ao clicar/focar nó, controles `<Controls />` / `<Background />` + toggle **Graph / Accessible Tree List** para navegação 100% linear por teclado e leitores de tela.
    3. **Model Card & Artifacts**: formato (`safetensors`/`onnx`/`pkl`), tamanho, `sha256` copiável, assinatura I/O e status de `SafetyScan` / `Experiment`.
- **Estados**: Loading, Empty (com CTA `+ Register Model`), Error, Populated, Modais (`Register Model/Version`, `Promote Stage & Alias`, `Version Diff Drawer`).

## 4. Acessibilidade (WCAG 2.2 AA)
- Contraste >= 4.5:1 para todo texto informativo (`text-zinc-400`+); cor sempre acompanhada de texto/padrão visual.
- Todos os gráficos SVG possuem `role="img"`, `<title>`, `<desc>` dinâmico e alternador **Chart / Data Table**.
- Matriz de confusão em `<table>` semântico (`<th scope="col|row">`); DAG com alternador `Graph / Tree List`.
- Alvos de toque >= 24×24px (preferência `h-7`/`h-8`); `motion-reduce:animate-none` em todos os pulsos e transições.
