# T9 — Web Canonical API, Sweeps/Registry/Lineage Screens & Accessibility

## Summary of Changes

- **Safety (`/safety`)**: Added `"not_evaluated"` compliance status, nullable `pass_rate`, and `"paused"` sweep status (`src/lib/types.ts`, `src/lib/safety.ts`); fixed `summarizeScan` and `OwaspMatrix` so untested OWASP categories report `"not_evaluated"` instead of 100% pass; removed cross-scan leakage and `MOCK_FINDINGS` fallback; added `SafetyGauge` `not_evaluated` handling, empty-state CTA / `NewScanModal` wiring, fetch error banner, and duplicate-fetch guard in `SafetyDashboard`.
- **Canonical Training (`/training`) & Sweeps (`/sweeps`, `/sweeps/[sweepId]`)**: Fixed `canonicalSeriesToTrainingSeries` array checks for GPU utilization, GPU memory (MiB -> GB), and reward margins (`src/lib/training-canonical.ts`); moved URL/fetch side effects out of state updaters; added SVG Parallel Coordinates plot and Metric & Pruning Curve with best-so-far envelope and pruned markers in `sweep-detail-client.tsx`.
- **Registry (`/models`) & Lineage (`/lineage`)**: Preserved `root`, `direction`, `depth`, and `truncated` in `adaptLineageGraph`; suppressed duplicate truncated banner inside `LineageExplorerClient`; widened `COL_GAP` to `360`; added `ariaLabel` on ReactFlow edges and async request-counter race guards in `loadModelDetail` and `WebhooksTab`.
- **Vectors, Gateway, Sandboxes, Benchmarks, Datasets & Shell**: Propagated `simulated`/`local_sandbox` indicators and top `<SimulatedBadge>` banners; removed hardcoded fallback metrics/sandbox IDs; aligned gateway/sandbox/vectors/shell controls with WCAG 2.2 AA (`role="tablist"`/`"tab"`, `aria-pressed`, `aria-expanded`, `tabIndex={0}` scrollable regions, `text-zinc-400` contrast, keyboard Escape/focus restoration, `<svg role="group">`, `overflow-y-auto` sidebar nav).

## Reviews & Rulings

- Reviewed by `design-director`, `a11y-architect`, and `react-reviewer`; all confirmed CRITICAL/HIGH findings were fixed prior to verification.
- **Dark-only UI Ruling**: Logged in `docs/atlas/2026-11-10-plataforma-completa/ledger.md` — `apps/web` retains dark-only theming (`#09090b` / `zinc-950`, `color-scheme: dark` in `apps/web/src/app/globals.css`).
- **Deferred Follow-up (LOW)**: `apps/web/package.json` currently has no `lint` script or `eslint` / `eslint-plugin-react-hooks` / `eslint-plugin-jsx-a11y` devDependencies; left out of T9 scope to avoid unrelated lint churn across existing G0/G1 files.

## Verification & Artifacts

- `npx tsc --noEmit && npx vitest run && npx next build`: 46 test files, 519 tests passed, build exited 0.
- `node e2e/t9-a11y-shots.cjs`: 13 views passed axe + DOM accessibility checks (`0 failed check(s)`), generating 17 screenshots under `docs/atlas/2026-11-17-reconciliacao/screenshots/`.
- `node e2e/g0.cjs http://localhost:3100 /tmp/g0-check` (mock mode, 300 ms simulated latency): exited 0 (`all checks passed`).
