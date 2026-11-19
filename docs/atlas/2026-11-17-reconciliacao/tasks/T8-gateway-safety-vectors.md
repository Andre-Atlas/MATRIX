# T8 — Gateway, safety (red-team) e vetores: sem mock silencioso, por projeto, estado compartilhado, rótulo `simulated`

`[core]` · nível **C4**, gatilho de segurança (chaves de provedor, rate limit, isolamento entre projetos) · ATLAS: opus/medium + revisão de segurança (Antigravity: `pro`) · Depende de: T4 (concluída). Paralelizável com T5/T7.
Leia primeiro: [HANDOFF.md](../HANDOFF.md), `docs/adr/013-reconciliacao-main-branch.md`, `analise.md` (§3 e a tabela de módulos de amplitude), `t2-notes.md`, `t4-notes.md`, `docs/adr/011-gateway.md` (desenho original), `docs/adr/009-analytics.md`.
Código: `server/src/matrix_server/gateway/` (~1281 linhas), `api/gateway.py`, `safety/` (~1018), `api/safety.py`, `vectors/` (~731), `api/vectors.py`, testes `tests/test_ai_gateway.py`, `test_safety_scanner.py`, `test_vectors_*.py`.

## Gateway — problemas do main a fechar
1. **Sem mock silencioso.** `mock_fallback_on_no_key=True` devolve resposta falsa quando falta a chave do provedor: troque por `503 provider_not_configured` (pode citar o NOME da variável esperada, nunca valor). O provedor `mock` só roda quando a rota/alvo pede `provider: "mock"` explicitamente; toda resposta e todo log dele carregam `simulated=true` (`x_matrix.simulated` no corpo, cabeçalho `x-matrix-simulated: true`, coluna no log).
2. **Isolamento por projeto.** Stats/providers/rules/logs/cache eram globais: tudo por projeto (chave de cache inclui `project_id`; endpoints filtram por `principal.project_id`; teste de isolamento em cada endpoint).
3. **Estado compartilhado.** Rate limit RPM/TPM, circuit breaker e logs em memória por processo não valem com 2+ workers. Persista em Postgres (fragmento): `gateway_rate_windows(project_id, scope, window_start, count/tokens)` com incremento atômico (`INSERT … ON CONFLICT DO UPDATE` no Postgres, equivalente no SQLite), janela fixa de 60 s, limite checado **antes** de chamar o provedor e corrigido com o uso real depois; e `gateway_requests(project_id, ts, rota/modelo, provider, status, latência, tokens, custo, cache_hit, simulated, erro sanitizado)` com retenção configurável e limpeza oportunista limitada. `stats` lê do banco. Cache pode continuar em memória por processo **se documentado** (chave com `project_id`).
4. **Parâmetros:** `payload.update(kwargs)` repassa campos arbitrários ao provedor ⇒ allowlist por provedor (`model, messages, temperature, top_p, max_tokens, stop, tools/tool_choice, response_format…`); o resto é descartado e listado em `x-matrix-warnings`; tetos configuráveis para `n` e `max_tokens`.
5. **Streaming:** se `stream: true` é aceito no schema mas não há SSE ⇒ `422 stream_not_supported` (documente que SSE vem depois).
6. **Segredos:** chaves de provedor só de variáveis de ambiente do operador; URL base só do operador (nunca do corpo); erros sanitizados (sem chave, sem URL completa, sem corpo do provedor além de 200 chars). Chaves virtuais (ADR-011) **ficam fora** desta tarefa: registre como adiado em `t8-notes.md`.

## Safety (red-team)
7. Sem alvo real (sem `simulated_outputs` e sem `response_generator`) o scan usa hoje uma resposta defensiva fixa e **todas as sondas passam (score 100)**: falso positivo enganoso. Mude: scan sem alvo ⇒ status `not_evaluated`, score `null`, mensagem clara; com saídas simuladas fornecidas pelo cliente ⇒ resultado válido mas `simulated=true`; só alvo real (gerador que chama gateway/endpoint via egress) ⇒ `simulated=false`. Campo/coluna `simulated` em `safety_scans` (fragmento) e no schema. Isolamento por projeto e papéis (`require`).

## Vetores
8. Sem UMAP/HDBSCAN/numpy; embedding padrão por hash de 3-gramas: rotule `engine` e `simulated`/`demo` nos resultados de projeção e drift (ex.: `engine: "pca-lite"`, `embedding: "hash-3gram (demo)"`, `simulated: true`) na API e no schema. Não implemente UMAP/HDBSCAN agora (ADR-009 segue o destino); documente a interface `ProjectionEngine` com stub `umap`/`hdbscan` que levanta `EngineUnavailable` se a biblioteca não estiver instalada. Cubra a projeção com teste (hoje sem teste): determinismo e forma da saída.

Atualize os testes do main dessas áreas à nova realidade **sem apagá-los** (troque o que testava mock silencioso/score 100 sem alvo e justifique em `t8-notes.md`).

## Migração
Fragmento `server/migrations/fragments/t8_<nome>.py` (tabelas do gateway, coluna `simulated`) + teste de fragmento sobre o esquema da 0008. **Não** crie arquivo em `migrations/versions/`.

## Pronto quando
`ruff` limpo; suíte completa verde com bancos reais; `check_skips` aceita; inventário de rotas ok; um teste prova que 2 apps/processos sobre o mesmo banco **compartilham** o rate limit; `t8-notes.md` com decisões, adiados e arquivos tocados.
