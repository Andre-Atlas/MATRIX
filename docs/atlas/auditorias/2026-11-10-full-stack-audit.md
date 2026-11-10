# Auditoria full-stack-audit — 2026-11-10
Escopo: Full-Stack MATRIX (`server/src/matrix_server` + `apps/web/src`) · Dimensões: `silent-failure-hunter`, `react-reviewer`, `fastapi-reviewer`
Resumo: 2 CRITICAL, 2 HIGH, 1 MEDIUM, 0 LOW (0 não confirmados)

## Achados

### CRITICAL — Divergência de contrato entre `/api/v1/safety/*` e UI `/safety` (422 em novo scan, `NaN%` Compliance e 0 findings)
Dimensões: `fastapi-reviewer`, `react-reviewer`, `silent-failure-hunter` · Status: confirmado
Evidência:
- `server/src/matrix_server/api/safety.py:37-44` (`CreateSafetyScanRequest` com `ConfigDict(extra="forbid")` aceita apenas `target_prompt`, `target_model`, `categories`, `simulated_outputs`; a UI em `apps/web/src/components/safety/new-scan-modal.tsx` envia `name`, `target_system_prompt`, `max_probes_per_category`, resultando em `HTTP 422 Unprocessable Entity`).
- `server/src/matrix_server/api/safety.py:70-177` e `server/src/matrix_server/safety/runner.py:37-55` (`SafetyScanReport` e endpoints `/scans` retornam `global_score`, `passed_count`, `failed_count` e `categories` como dict indexado por ID; o frontend `SafetyScan` espera `safety_score`, `probes_passed`, `probes_failed`, `risk_level` e `category_scores`, gerando `NaN%` Compliance e score `%` em branco).
- `server/src/matrix_server/api/safety.py:46-67` (`GET /api/v1/safety/categories` omite `code` e `short_description`, fazendo o modal exibir `Probes: 1/1` e quebrando a seleção de categorias).
- `server/src/matrix_server/safety/evaluator.py` e `server/src/matrix_server/safety/runner.py:124-138` (`SafetyFinding` não inclui `id`, `scan_id`, `probe_name`, `attack_prompt`, `observed_output`, `defense_analysis`, `recommended_guardrail`, `status`, resultando em `Showing 0 of 0 findings` e crash ao filtrar/buscar na tabela de findings).
Impacto: A vertical `/safety` (OWASP LLM Top 10) fica inoperante em dados reais: criação de scans falha com HTTP 422, KPIs exibem `NaN%` e vulnerabilidades detectadas não aparecem ou quebram a renderização ao pesquisar.
Correção sugerida: Alinhar o schema de request/response em `server/src/matrix_server/api/safety.py` (ou adicionar adapter em `apps/web/src/lib/safety.ts`) para aceitar `name`, `target_system_prompt` e `max_probes_per_category`, expor `code` e `short_description` nas categorias, e mapear os campos de `SafetyScan` e `SafetyFinding` esperados pelos componentes de `/safety`.

### CRITICAL — Endpoint `GET /api/v1/gateway/rules` ausente (404) e campos faltantes de telemetria/logs no AI Gateway
Dimensões: `fastapi-reviewer`, `react-reviewer`, `silent-failure-hunter` · Status: confirmado
Evidência:
- `server/src/matrix_server/api/gateway.py:13-133` (O router define `/chat/completions`, `/stats`, `/providers` e `/logs`, mas não implementa `GET /api/v1/gateway/rules`, que é requisitado por `apps/web/src/app/gateway/page.tsx`, gerando `404 Not Found` engolido por `.catch(() => null)`).
- `server/src/matrix_server/gateway/router.py:372-387` e `server/src/matrix_server/api/gateway.py:44-74` (`GatewayRequestLog.to_dict()` aninha tokens em `tokens: {prompt_tokens, completion_tokens, total_tokens}` sem `status_code` ou `prompt_tokens`/`completion_tokens` no topo; `GatewayStatsResponse` e `ProviderStatusItem` também omitem campos consumidos pela UI, causando `0 in / 0 out` tokens e `HTTP undefined` ao expandir linhas em `apps/web/src/components/gateway/gateway-logs-table.tsx`).
Impacto: Painel de regras de roteamento em `/gateway` nunca carrega dados reais do servidor (404 silencioso) e a tabela de auditoria de requisições exibe `0 in / 0 out` e `HTTP undefined`.
Correção sugerida: Implementar `GET /api/v1/gateway/rules` em `server/src/matrix_server/api/gateway.py` e enriquecer `GatewayRequestLog.to_dict()`, `GatewayStatsResponse` e `ProviderStatusItem` (ou normalizar em `apps/web/src/lib/gateway.ts`) com `prompt_tokens`, `completion_tokens`, `status_code` e métricas completas dos provedores.

### HIGH — Problemas de Autenticação, Contexto de Projeto em API Key Mode e Proxy Next.js (`/api/proxy/[...path]`)
Dimensões: `fastapi-reviewer`, `react-reviewer`, `silent-failure-hunter` · Status: confirmado
Evidência:
- `apps/web/src/app/api/proxy/[...path]/route.ts:13,40-41,49` (Prefixa incondicionalmente `/api/v1/${path.join("/")}`, gerando `/api/v1/api/v1/...` caso chamado com `/api/v1/...`; não exporta o método `PATCH`, quebrando endpoints como `PATCH /api/v1/projects/{project_id}/members/{user_id}`; usa `res.headers.get("set-cookie")` em vez de `getSetCookie()`, descartando múltiplos cookies).
- `apps/web/src/components/auth/user-nav.tsx:20-36,54` (`useEffect` com array de dependências vazio `[]` não reexecuta em mudanças de rota via `usePathname()` após login/logout; linha 54 oculta o contexto de projeto quando `profile.is_api_key && !profile.user`).
- `server/src/matrix_server/api/auth.py:145-151` (`GET /api/v1/auth/me` com `is_api_key=True` retorna apenas `project`, omitindo `current_project` e `projects`).
Impacto: Operações `PATCH` via proxy falham com `405 Method Not Allowed`, navegação pós-login mantém estado obsoleto na sidebar até reload manual, e o modo API Key esconde o projeto ativo no `UserNav`.
Correção sugerida: Exportar `PATCH` em `apps/web/src/app/api/proxy/[...path]/route.ts`, sanitizar prefixo duplicado `api/v1`, propagar `getSetCookie()`, incluir `current_project` e `projects` em `GET /api/v1/auth/me` para `is_api_key=True` e atualizar `UserNav` com `usePathname()`.

### HIGH — Divergência de campos no adaptador `/vectors` (`adaptVectorsData`) causando `Invalid Date` e metadados ausentes
Dimensões: `react-reviewer`, `silent-failure-hunter` · Status: confirmado
Evidência:
- `apps/web/src/lib/vectors.ts:187-212,239-263` (`adaptVectorsData` mapeia `top_keywords` em vez de `keywords`, `created_at` em vez de `last_detected`, `unanswered_query_count` em vez de `frequency`, `recommended_action` em vez de `suggested_action`, e omite a propriedade `name` nos objetos de métricas de drift `jensen_shannon`, `psi`, `centroid_cosine` e `wasserstein`).
Impacto: Na página `/vectors`, os cards de Knowledge Gaps exibem `Invalid Date`, contagens de frequência/ações sugeridas ficam vazias ou indefinidas, e cards de métricas de drift perdem seus rótulos nominais.
Correção sugerida: Atualizar `adaptVectorsData` em `apps/web/src/lib/vectors.ts` para popular ambos os conjuntos de chaves (`keywords`/`top_keywords`, `last_detected`/`created_at`, `frequency`/`unanswered_query_count`, `suggested_action`/`recommended_action`) e incluir `name` em cada métrica de drift.

### MEDIUM — Warning `output: "standalone"` com `next start`, hidratação SSR de fuso horário e classes Tailwind inválidas
Dimensões: `react-reviewer` · Status: confirmado
Evidência:
- `apps/web/next.config.mjs` (`output: "standalone"` emite warning ao iniciar com `next start` padrão).
- `apps/web/src/components/auth/user-nav.tsx:91` e componentes de UI (uso de classes utilitárias não-padrão como `py-0.2` e `backdrop-blur-xs`, além de formatação de datas dependente de timezone local durante SSR/hidratação).
Impacto: Warnings no boot de produção/desenvolvimento, risco de mismatch de hidratação em timestamps renderizados no servidor e estilos de padding/blur ignorados pelo compilador CSS.
Correção sugerida: Ajustar script de start ou configuração `standalone` em `apps/web/next.config.mjs`, padronizar formatação de datas em UTC/client-safe e substituir `py-0.2` / `backdrop-blur-xs` por classes Tailwind válidas (`py-0.5`, `backdrop-blur-sm`).

## Backlog
- `fix` · M — Alinhar contrato de `/api/v1/safety/*` (`CreateSafetyScanRequest`, `SafetyScan`, `SafetyCategory`, `SafetyFinding`) com a UI `/safety` e adaptador frontend (CRITICAL, confirmado)
- `fix` · M — Implementar `GET /api/v1/gateway/rules` e incluir campos de tokens/status/métricas em `GatewayRequestLog.to_dict()`, `GatewayStatsResponse` e `ProviderStatusItem` (CRITICAL, confirmado)
- `fix` · P — Corrigir proxy Next.js (`PATCH`, deduplicação de `/api/v1`, `getSetCookie()`), retorno de `/api/v1/auth/me` em modo API Key e refresh de `UserNav` por `usePathname()` (HIGH, confirmado)
- `fix` · P — Corrigir mapeamento de campos em `adaptVectorsData` (`apps/web/src/lib/vectors.ts`) para `keywords`, `last_detected`, `frequency`, `suggested_action` e `name` nas métricas de drift (HIGH, confirmado)
- `refactor` · P — Ajustar `output: "standalone"` em `next.config.mjs`, substituir classes Tailwind inválidas (`py-0.2`, `backdrop-blur-xs`) e estabilizar formatação de timezone na hidratação SSR (MEDIUM, confirmado)
