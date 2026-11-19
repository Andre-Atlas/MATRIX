# T9 — Web: telas das áreas do `main` + sweeps + registry sobre a pilha canônica

`[design]` · nível **C3/C4** · ATLAS: sonnet/medium + design-director (Antigravity: `pro`) · Depende de: T5, T6, T7, T8 (as APIs precisam existir). Leia [HANDOFF.md](../HANDOFF.md), `docs/atlas/2026-11-10-plataforma-completa/contracts/g2.md` e `g3.md`, e o brief de design em `docs/atlas/2026-11-10-plataforma-completa/`.

## Fazer
1. Páginas do `main` hoje sob `apps/web/src/app/(app)/` (mlops, benchmarks, sandboxes, gateway, safety, vetores, datasets): troque o cliente de API pelo cliente canônico (cookies `__Host-`, proxy endurecido), remova qualquer uso de JWT/localStorage e de `/mlops/*` deprecated.
2. Telas novas: **Sweeps** (lista, detalhe com runs, melhor, importância, curva de poda, pausar/retomar/parar), **Registry** (versões, histórico de estágios, aliases, webhooks com segredo exibido uma vez), **Linhagem** (grafo limitado com aviso `truncated`).
3. **Rótulo `simulated`:** todo resultado com `simulated=true` (benchmarks local_sim, safety, gateway mock, vetores demo) mostra selo visível "simulado" (não só cor), com texto acessível.
4. Estados de loading/vazio/erro em toda tela; teclado e leitor de tela (WCAG 2.2 AA); sem `loading.tsx` e sem `<Suspense>` sem `key` com `searchParams` (travam a navegação).
5. Remova `mlops_compat`/`views` do web quando nada mais os usar.

## Pronto quando
`cd apps/web && npx tsc --noEmit && npx vitest run && npx next build` verdes (base: 494 testes); testes novos por tela; checagem de acessibilidade (axe) nas telas novas; capturas em `docs/atlas/.../screenshots/` das telas novas (claro e escuro).
