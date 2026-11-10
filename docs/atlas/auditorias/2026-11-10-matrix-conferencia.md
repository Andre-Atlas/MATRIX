# Auditoria matrix — 2026-11-10
Escopo: repositório MATRIX, commit publicado `5a28801` + diff não commitado (trabalho em andamento do ciclo 2, marcado **[WIP]**) · Dimensões: produção, segurança, falhas silenciosas, testes
Resumo: 0 CRITICAL, 4 HIGH, 24 MEDIUM, 15 LOW (2 não confirmados)

## Verificação executada

Pelo orquestrador (primeira mão, 2026-11-10):
- **HEAD, servidor** (archive limpo): ruff limpo; 348 passed, 131 skipped (os pulados exigem Postgres/ClickHouse).
- **HEAD, web**: `tsc` ok, 97 testes, `next build` compilou.
- **HEAD, SDK**: 67 passed, 1 failed (`tests/test_tracking.py::test_start_run_body`, `KeyError: 'git'`, porque o teste só passa dentro de um clone git; na árvore real ele passa).
- **Árvore de trabalho (WIP)**: 982 testes coletados sem erro; 571 passed, 3 failed, 408 skipped. Ruff com 4 erros triviais (`storage/models.py:427` ordem de imports e `noqa` inútil; `tests/test_run_store.py:167` e `:183`). As 3 falhas são em `tests/test_runs_api.py`: duas são do próprio teste (manda float NaN/inf com `json=`, o httpx recusa; o contrato pede as strings `"nan"`/`"inf"`). A terceira (`test_metric_keys_series_downsampling_and_resend[sqlite-memory]`, contagem 10000 vs 10001 após reenvio) pode ser bug real do T26, que foi interrompido.
- **Compose**: com o daemon Docker ligado, `docker run minio/minio` falhou com "pull access denied for minio/minio, repository does not exist or may require 'docker login'", e `quay.io/minio/minio` respondeu 401.
- Nenhum serviço estava rodando (ClickHouse, Postgres, Docker e S3 desligados de propósito).

Pelo audit-lead (reprodução na cópia `head`, scripts descartáveis em `scratchpad/review4/`):
- F1: um teste descartável sobre as fixtures de `tests/test_experiments.py`, com um provider que falha em todas as células, resultou em `STATUS done ERROR None`, summary todo `None` e `REGRESSIONS []` contra uma baseline boa.
- F2: sem `MATRIX_JUDGE_PROVIDER`, `build_eval_deps()` e `online_handlers.build_deps(None)` devolvem `HeuristicJudge`. O score online é gravado com `source="llm"` fixo (`workers/online_handlers.py:204`).
- F8: script com `start_run` seguido de `sys.exit(1)` contra o `FakeServer` do SDK terminou com o PATCH `{'status': 'finished'}`. No controle, com exceção não tratada, o PATCH foi `{'status': 'failed', 'error': 'RuntimeError: boom'}`.
- Compose (`deploy/compose.yaml`) e o proxy web lidos linha a linha. Procura por avisos em `README.md` e `server/README.md`, e por uso de S3/MinIO no código da HEAD.

## Escopo e dimensões

- **Rodadas:** produção (production-audit), segurança (security-reviewer), falhas silenciosas (silent-failure-hunter), testes (pr-test-analyzer).
- **Não rodadas:** performance, fluxos de interface, apps com LLM e comentários. Ficaram de fora para poupar o limite de uso. Ninguém pediu esse corte: foi uma decisão do orquestrador e **precisa ser confirmada**. Por isso o relatório não cobre essas dimensões. Um exemplo: a remoção dos `loading.tsx` registrada no report do ciclo 1 é assunto de fluxos de interface e não foi auditada.

## Verificação adversarial dos HIGH (uma linha cada)

- **S4 (MinIO exposto), rebaixado para MEDIUM (fundido em M3):** a exposição é real (portas em 0.0.0.0, sem credenciais). Mas na HEAD nenhum serviço usa S3/MinIO (zero referências em `server/src`, `apps/web/src` e no `pyproject`), e a imagem nem baixa. Não há dado em risco hoje. O achado volta a HIGH quando o artifact store S3 do WIP apontar para esse serviço.
- **MinIO não baixa, HIGH mantido (H1):** `deploy/compose.yaml:35` usa `image: minio/minio`, e o pull falha (reproduzido pelo orquestrador). Assim `make up` (`docker compose up --build`), o caminho de deploy documentado no README, não sobe a stack.
- **S1 (proxy web sem login), HIGH mantido (H2):** o risco foi aceito no ciclo 1 com a condição "não exponha a web fora de rede privada". Esse aviso só existe em `docs/atlas/2026-11-09-plataforma-mvp/report.md:68`. Ele não aparece no `README.md` nem no `server/README.md` (grep sem resultado), e o próprio compose publica `3000:3000` em todas as interfaces. A condição de aceite não é cumprida pelo artefato entregue.
- **F1 (experimento "done" com todas as células falhando), HIGH mantido (H3):** reproduzido. Status `done`, `error=None`, nenhuma regressão contra a baseline.
- **F2 (juiz heurístico gravado como "llm"), HIGH mantido (H4):** reproduzido. O padrão é documentado em `server/README.md`, mas o dado gravado (`source="llm"`) não distingue juiz heurístico de LLM. O compose não repassa `MATRIX_JUDGE_PROVIDER` nem chaves, então na stack do compose não há como ter juiz real sem editar o arquivo.

## Achados

### HIGH — `docker compose up` não sobe: imagem `minio/minio` não pode ser baixada
Dimensões: produção (novo, evidência do orquestrador); segurança (S6 citou a imagem sem tag) · Status: confirmado (reproduzido pelo orquestrador; referência à imagem verificada pelo lead)
Evidência: `deploy/compose.yaml:34-41` (`minio: image: minio/minio`, sem tag); `docker run minio/minio` → "pull access denied for minio/minio, repository does not exist or may require 'docker login'"; `quay.io/minio/minio` → 401. `Makefile:19-20` (`up: docker compose -f deploy/compose.yaml up --build`). Na HEAD nenhum código usa S3/MinIO (grep vazio em `server/src`, `server/pyproject.toml`, `apps/web/src`). O report do ciclo 1 (`report.md:70`) registra que o compose nunca foi executado.
Impacto: o deploy documentado (`make up`, README "stack completa via docker compose") falha no pull, e nenhum serviço sobe. O serviço quebrado não é usado por nada na HEAD.
Correção sugerida: tirar o serviço `minio` e o volume `minio-data` do compose até o artifact store S3 do ciclo 2 entrar. Quando entrar, usar imagem S3-compatível disponível, fixada por digest. Acrescentar ao CI `docker compose config` e um `docker compose pull`/build de fumaça.

### HIGH — Web sem autenticação usa a `MATRIX_API_KEY` global, e o aviso do risco aceito não está onde o operador lê
Dimensões: segurança (S1), produção (P5, parte "sem aviso web sem login") · Status: confirmado (leitura do código e do compose pelo lead)
Evidência: `apps/web/src/app/api/proxy/[...path]/route.ts:18-19` injeta `authorization: Bearer ${MATRIX_API_KEY}` em toda requisição, sem checar quem chama. Não existe `middleware.ts` em `apps/web` (nem na HEAD, nem no WIP; o web não mudou no WIP). `deploy/compose.yaml:77-78` publica `"3000:3000"` em todas as interfaces. O aviso "Não exponha a web fora de rede privada" só está em `docs/atlas/2026-11-09-plataforma-mvp/report.md:68`; `README.md` e `server/README.md` não o mencionam (grep por login/privad/expor sem resultado).
Impacto: quem alcança a porta 3000 tem leitura e escrita completas no projeto da chave configurada (traces, prompts, datasets, experimentos, anotações). O risco foi aceito no ciclo 1 sob uma condição que o artefato entregue não comunica e que o compose contradiz. Atenuante: com `MATRIX_API_KEY` vazio (padrão do compose) o proxy não injeta credencial, mas aí a UI fica inútil (ver P5).
Correção sugerida: agora (small), publicar a web em `127.0.0.1:3000:3000` e pôr o aviso de risco no README raiz e no `apps/web`. Definitivo (large), login e RBAC do G0 no ciclo 2, com o proxy repassando a sessão do usuário em vez da chave global.

### HIGH — Experimento termina `done`, `error=None`, mesmo com todas as células falhando
Dimensões: falhas silenciosas (F1) · Status: confirmado (reproduzido pelo lead)
Evidência: teste descartável em `scratchpad/review4/test_audit_repro.py::test_f1_all_cells_fail` (provider que sempre levanta `ProviderError(401)`). Saída: `STATUS done ERROR None`; `CELL_ERRORS ['down request failed (status 401): invalid api key', ...]`; `SUMMARY {'p/m': {'equals': {'mean': None, 'pass_rate': None}, ...}}`; `REGRESSIONS []` contra baseline boa. Código: `server/src/matrix_server/evals/runner.py:205-216` (erro fica na célula, scores n/a), `:371-372` e `:420-421` (`exp.status = "done"; exp.error = None` sem contar células com erro); `summarize` ignora `None`; `find_regressions` trata `None` como sem regressão.
Impacto: chave de API errada, modelo inexistente ou provider fora do ar produzem um experimento "concluído" e sem regressões. Quem compara variantes ou usa a baseline como gate recebe um falso verde.
Correção sugerida: contar células com erro. Se todas falharam, marcar `failed` com o erro mais frequente; se só algumas, expor `error_cells`/`error_rate` no experimento e no summary. Em `find_regressions`, tratar baseline com valor e atual `None` como regressão ou "indeterminado", nunca como ok. A web deve mostrar a taxa de erro no topo.

### HIGH — Juiz padrão é o `HeuristicJudge` (só para teste/demo) e o score sai gravado como `source="llm"`; o compose não permite trocá-lo
Dimensões: falhas silenciosas (F2), produção (P10) · Status: confirmado (reproduzido pelo lead)
Evidência: `scratchpad/review4/test_audit_repro.py::test_f2_default_judge` → `EXPERIMENT_JUDGE HeuristicJudge ONLINE_JUDGE HeuristicJudge` sem `MATRIX_JUDGE_PROVIDER`. `evals/runner.py:66-69` e `workers/online_handlers.py:111-118` caem em `fake`. `evals/judge.py:283-284` diz "Offline deterministic judge based on lexical overlap. FOR TESTS/DEMO ONLY". `workers/online_handlers.py:204` grava `source="llm"` fixo. `deploy/compose.yaml:46-50` (`server-env`) repassa só 4 variáveis, sem `MATRIX_JUDGE_PROVIDER`, `MATRIX_JUDGE_MODEL`, `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`. Atenuante: `server/README.md` documenta o padrão `fake`.
Impacto: faithfulness, relevância e demais métricas de juiz são sobreposição lexical apresentada como avaliação por LLM. Regras e alertas disparam sobre essas notas. Na stack do compose o juiz é sempre o heurístico, e o dado não deixa diferenciar depois.
Correção sugerida: gravar o juiz efetivo (`judge: heuristic|openai:<modelo>`) no score e no experimento, e usar `source="heuristic"` quando for o caso. Na partida do worker, logar aviso alto quando o juiz for `fake`. Repassar no compose `MATRIX_JUDGE_PROVIDER`, `MATRIX_JUDGE_MODEL`, `MATRIX_OPENAI_BASE_URL` e as chaves (`${VAR:-}`). A UI deve mostrar um selo "juiz heurístico".

### MEDIUM — `test_start_run_body` exige estar dentro de um clone git
Dimensões: testes (T5) · Status: confirmado (reproduzido pelo orquestrador e pelo auditor)
Evidência: `sdk/python/tests/test_tracking.py:34` (`assert body["git"]["commit"] ... # tests run inside the repository`); no archive limpo → `KeyError: 'git'`.
Impacto: `make test` falha fora de um clone (tarball, build de pacote, CI com checkout sem `.git`).
Correção sugerida: criar um repositório git temporário com `tmp_path` no teste, ou separar o teste de git com `skipif` explícito quando não houver `.git`.

### MEDIUM — [WIP] Redirect cross-origin no egress repassa `x-api-key`, `api-key` e `x-goog-api-key`
Dimensões: segurança (S12) · Status: confirmado (leitura do código pelo lead)
Evidência: `server/src/matrix_server/egress/client.py:40` (`_CREDENTIAL_HEADERS = {"authorization", "proxy-authorization", "cookie", "cookie2"}`) e `:246-248` (só esses são removidos quando a origem muda).
Impacto: um provider ou URL configurável que redirecione para outra origem recebe a chave da Anthropic, da Azure ou do Google.
Correção sugerida: remover na troca de origem todos os cabeçalhos de credencial conhecidos, ou adotar lista de permissão (repassar só `content-type`/`accept`/`user-agent`).

### MEDIUM — Compose publica ClickHouse e MinIO em todas as interfaces, sem senha
Dimensões: segurança (S4, S5), produção (P6) · Status: confirmado (leitura do compose pelo lead); S4 rebaixado de HIGH
Evidência: `deploy/compose.yaml:6-11` (`"8123:8123"`, `CLICKHOUSE_USER: default` sem `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1"`); `:34-39` (MinIO `9000`/`9001` publicados, sem `MINIO_ROOT_USER/PASSWORD`, padrão `minioadmin`); `:25,47` (`matrix:matrix` fixo; o Postgres **não** publica porta, o que reduz o impacto dessa parte).
Impacto: o ClickHouse guarda todos os spans (prompts, saídas, contexto). Com 8123 aberto e sem senha, a leitura é direta. Não confirmei se a imagem `24.8` restringe o usuário `default` sem senha à rede local; por isso o achado não sobe. O MinIO não guarda nada na HEAD (motivo do rebaixamento de S4) e vira HIGH quando o S3 do WIP usá-lo.
Correção sugerida: tirar `ports` do ClickHouse e do MinIO, ou publicar só em `127.0.0.1`. Exigir senhas por variável sem padrão (`${CLICKHOUSE_PASSWORD:?}`, `${MINIO_ROOT_PASSWORD:?}`, `${POSTGRES_PASSWORD:?}`) e montar as URLs a partir delas.

### MEDIUM — `/health` não indica prontidão; worker e web sem healthcheck
Dimensões: produção (P3) · Status: confirmado (auditor)
Evidência: `server/src/matrix_server/app.py:39-41` (HEAD; só devolve ok); `deploy/compose.yaml:53-57` usa só esse endpoint; `worker` e `web` sem `healthcheck`.
Impacto: API "saudável" com banco ou ClickHouse fora; worker travado não é detectado.
Correção sugerida: `/ready` testando Postgres (`SELECT 1`) e o span store; healthcheck do worker por heartbeat; healthcheck HTTP do web.

### MEDIUM — `create_all` na partida da API e do worker, também em produção
Dimensões: produção (P4) · Status: confirmado (leitura do código pelo lead)
Evidência: `server/src/matrix_server/app.py:39` (`init_db(engine)  # dev/tests; production runs alembic upgrade head first`); `cli.py:32,71`; `storage/db.py:65-68` (`Base.metadata.create_all`).
Impacto: se a API ou o worker subir antes do `alembic upgrade head` (outro orquestrador, réplica, operador seguindo outro roteiro), as tabelas são criadas sem `alembic_version`. A migração seguinte quebra ou exige `stamp` manual.
Correção sugerida: chamar `init_db` só com `span_store=memory`/SQLite ou com flag explícita de dev. Em Postgres, falhar na partida se `alembic_version` não estiver no head.

### MEDIUM — SDK: `sys.exit(1)` registra o run como `finished`
Dimensões: falhas silenciosas (F8) · Status: confirmado (reproduzido pelo lead; o auditor não tinha reproduzido)
Evidência: `scratchpad/review4/f8_exit.py` contra o `FakeServer` de `sdk/python/tests/conftest.py`: `exit1` → `PATCH bodies: [{'status': 'finished'}]`; `raise` → `[{'status': 'failed', 'error': 'RuntimeError: boom'}]`. Código: `sdk/python/src/matrix_sdk/tracking.py:473-480` (`_atexit` só olha `sys.last_exc`/`last_value`, que o CPython não define em `SystemExit`).
Impacto: scripts de treino que terminam com `sys.exit(<código≠0>)` (argparse, guardas de NaN, `click`) aparecem como sucesso.
Correção sugerida: instalar um `sys.excepthook` e envolver `sys.exit` (ou registrar via `threading.excepthook` + checagem do código de saída). Uma opção mais simples: expor `with start_run(...)` e documentar que o atexit não distingue código de saída.

### MEDIUM — Testes pulados sem guarda no CI (Postgres/ClickHouse e importorskip)
Dimensões: testes (T3, T4) · Status: confirmado (auditor; números do orquestrador)
Evidência: 131 testes pulados localmente na HEAD (408 no WIP). `.github/workflows/ci.yml:28-30` define `MATRIX_TEST_DATABASE_URL`/`MATRIX_TEST_CLICKHOUSE_URL`, mas `:38` roda `uv run pytest -q` sem conferir skips. No SDK, `importorskip` de torch/transformers etc. também passa calado.
Impacto: se um serviço cair ou uma variável for renomeada, o CI fica verde com centenas de testes pulados. Os testes de Postgres e ClickHouse deixam de valer sem aviso.
Correção sugerida: no CI, definir `MATRIX_REQUIRE_SERVICES=1` e fazer as fixtures falharem em vez de pular, ou rodar `pytest -rs` com uma checagem de `skipped == 0` nas suítes de serviço.

### MEDIUM — Retenção/TTL do ClickHouse nunca é ligada
Dimensões: produção (P1) · Status: confirmado (leitura do código pelo lead no WIP)
Evidência: `server/src/matrix_server/storage/spans.py:544,552,578-579` (WIP; `536,570` na HEAD) aceita `ttl_days`; `build_span_store` (`:906` WIP, `:892` HEAD) não o repassa; não há campo no `Settings`. O TTL só aparece no `CREATE TABLE IF NOT EXISTS`, então ligá-lo depois não altera tabela existente.
Impacto: crescimento ilimitado dos spans; retenção não documentada (também é pergunta de privacidade).
Correção sugerida: `MATRIX_SPAN_TTL_DAYS` no `Settings`, repassado ao store; `ALTER TABLE ... MODIFY TTL` na partida ou numa migração; documentar.

### MEDIUM — [WIP] Reenvio de métricas conta 10001 pontos onde o esperado é 10000
Dimensões: testes (verificação do orquestrador) · Status: não confirmado
Evidência: `server/tests/test_runs_api.py::test_metric_keys_series_downsampling_and_resend[sqlite-memory]` falha com contagem 10000 vs 10001 após reenvio (WIP; T26 interrompido).
Impacto: se for bug do código, o reenvio idempotente de pontos não deduplica um ponto (provavelmente na borda do downsampling) e os contadores de série divergem.
Correção sugerida: concluir o T26 investigando a deduplicação na fronteira (ponto `step` repetido no último bloco), antes de mexer no teste.

### MEDIUM — Proxy web sai de `/api/v1` com `..`/`%2e%2e`
Dimensões: segurança (S2) · Status: não confirmado (o auditor testou `new URL` no node; não houve teste de ponta a ponta no Next)
Evidência: `apps/web/src/app/api/proxy/[...path]/route.ts:13` (`new URL(`${base}/api/v1/${path.join("/")}`)` normaliza `..`).
Impacto: com a chave global, a requisição alcança rotas da API fora de `/api/v1` (ex.: ingest OTLP, `/health`). As rotas de admin continuam exigindo o token de admin.
Correção sugerida: rejeitar segmentos `.`/`..` (já decodificados) e codificar cada segmento com `encodeURIComponent`; conferir que `url.pathname` começa com `/api/v1/`.

### MEDIUM — Proxy web lê o corpo como texto sem limite e descarta cabeçalhos de segurança da resposta
Dimensões: segurança (S3) · Status: confirmado (leitura do código pelo lead)
Evidência: `route.ts:23` (`body: hasBody ? await req.text() : undefined`); `:26-34` (só repassa `content-type` e `content-disposition`; caem `x-content-type-options`, `content-security-policy` sandbox e `cache-control` dos artefatos).
Impacto: uploads binários corrompidos e corpo inteiro em memória no Next. Artefatos inline perdem o CSP sandbox e o nosniff que o servidor define.
Correção sugerida: repassar `req.body` como stream (`duplex: "half"`) com limite; repassar a lista de cabeçalhos de segurança da resposta.

### MEDIUM — Documentação sem passos de bootstrap; `make up` sobe uma UI que responde 401 em tudo
Dimensões: produção (P5) · Status: confirmado (leitura do README e do compose pelo lead)
Evidência: `README.md` e `server/README.md` não citam `MATRIX_ADMIN_TOKEN`, `MATRIX_SPAN_STORE`, `MATRIX_CLICKHOUSE_URL` nem como criar o primeiro projeto e a chave; `deploy/compose.yaml:76` (`MATRIX_API_KEY: ${MATRIX_API_KEY:-}`) deixa o web sem chave por padrão.
Impacto: o primeiro uso via compose falha de forma opaca, e não há roteiro para ir de "subiu" a "tenho chave".
Correção sugerida: seção "Primeiro uso" no README com a sequência admin token → `POST /api/v1/admin/projects` → `MATRIX_API_KEY` do web; falhar a partida do web, com mensagem clara, se a chave estiver vazia.

### MEDIUM — Ação de regra pulada em silêncio, mas contada como disparada
Dimensões: falhas silenciosas (F4) · Status: confirmado (auditor)
Evidência: `server/src/matrix_server/rules.py:~228-246` (dataset apagado ou root da trace não achado → `return` sem log, com o disparo já contado).
Impacto: regras de "adicionar ao dataset" parecem funcionar e não fazem nada.
Correção sugerida: logar e registrar `skipped_reason` no disparo; não incrementar o contador de ações executadas.

### MEDIUM — Hook final de falha que dá erro deixa o experimento `running` para sempre
Dimensões: falhas silenciosas (F5) · Status: confirmado (auditor, leitura do código)
Evidência: `server/src/matrix_server/storage/jobs.py:67-87,101-107`; `workers/runner.py:64-70`; `workers/handlers.py:85-104`.
Impacto: o experimento fica preso como `running` na UI, sem erro e sem reprocessamento.
Correção sugerida: executar o hook numa transação própria com fallback mínimo (`UPDATE experiments SET status='failed'`) e logar a exceção do hook.

### MEDIUM — SDK: `finish()` pode abandonar ops/params no prazo e deixar o run `running` sem aviso
Dimensões: falhas silenciosas (F7) · Status: confirmado (auditor, leitura do código)
Evidência: `sdk/python/src/matrix_sdk/tracking.py:~537-541, ~427-432`.
Impacto: params e artefatos somem e o run fica "running" no servidor sem nenhuma mensagem para o usuário.
Correção sugerida: ao estourar o prazo, logar `warning` com a contagem de ops descartadas e enviar o PATCH de status mesmo assim.

### MEDIUM — Ganchos de ingestão engolem falhas e o OTLP responde 200
Dimensões: falhas silenciosas (F3) · Status: confirmado (auditor)
Evidência: `server/src/matrix_server/api/ingest.py:83-92,122-128`; `rules.py:~341-346`; `api/online_evals.py:94-97`.
Impacto: regras e avaliações online deixam de rodar para os spans afetados sem retry, outbox ou contador, e o cliente não fica sabendo.
Correção sugerida: enfileirar o gancho na fila `jobs` na mesma transação (outbox) em vez de chamar inline; contador/log de falhas.

### MEDIUM — [WIP] Throttle de login por (e-mail, IP) é contornável e caro atrás do proxy Next
Dimensões: segurança (S9) · Status: confirmado (leitura do código pelo lead)
Evidência: `server/src/matrix_server/users.py:239-241` (`attempt_key` = e-mail + IP); `api/auth.py:98-110, 327-328`; cada tentativa custa argon2 de 64 MiB.
Impacto: atrás do proxy Next todos os usuários compartilham o mesmo IP (um bloqueia o outro), ou o IP vem de cabeçalho forjável. Além disso, uma enxurrada de tentativas com e-mails distintos esgota memória e CPU.
Correção sugerida: throttle adicional por e-mail (global) e por IP real (cabeçalho só de proxy confiável); semáforo limitando hashes argon2 simultâneos.

### MEDIUM — [WIP] Presigned PUT do S3 sem tamanho na assinatura
Dimensões: segurança (S10) · Status: confirmado (leitura do código pelo lead)
Evidência: `server/src/matrix_server/artifacts/s3.py:253-262` (`Params` só com `Bucket`, `Key` e `ContentType`; `size` é recebido e não entra na assinatura); `artifacts/service.py:169`.
Impacto: o cliente sobe até 5 GiB por URL, fora da quota.
Correção sugerida: incluir `ContentLength` assinado ou usar presigned POST com `content-length-range`; conferir o tamanho no `complete`.

### MEDIUM — [WIP] Sem verificação de hash acima de 256 MiB: envenenamento de sha deduplicado
Dimensões: segurança (S11) · Status: confirmado (auditor)
Evidência: `server/src/matrix_server/artifacts/service.py:298-331`; `s3.py:160-168`; `local.py:97-100`.
Impacto: um cliente declara o sha de um artefato legítimo e sobe outro conteúdo; uploads futuros com o mesmo sha deduplicam para o conteúdo falso.
Correção sugerida: verificar hash em streaming em todos os tamanhos (ou checksum SHA-256 nativo do S3) e não deduplicar contra objeto não verificado.

### MEDIUM — Padrões inseguros sem validação na partida (memory/SQLite)
Dimensões: produção (P2) · Status: confirmado (auditor)
Evidência: `server/src/matrix_server/config.py`; `cli.py:12-20,30-38`; `workers/online_handlers.py:43-57` (com `span_store=memory`, o worker não enxerga os spans da API).
Impacto: um deploy sem variáveis sobe "funcionando" com dados voláteis, e as avaliações online nunca acham o span (retry infinito).
Correção sugerida: exigir `MATRIX_ENV=dev` para memory/SQLite; recusar worker com `span_store=memory` em processo separado.

### MEDIUM — `make demo`, o único teste e2e, não roda em lugar nenhum automatizado
Dimensões: testes (T1) · Status: confirmado (leitura do Makefile e do CI pelo lead)
Evidência: `Makefile:24-26`; `.github/workflows/ci.yml` sem `verify_mvp`.
Impacto: os critérios do MVP podem regredir sem o CI ver.
Correção sugerida: job de CI com os serviços já definidos rodando `make demo`.

### MEDIUM — Testes de concorrência do worker dependem de sleeps e prazos reais
Dimensões: testes (T6) · Status: confirmado (auditor)
Evidência: `server/tests/test_hardening.py:101` (lease 0.4 s vs 1.2 s), `:131` (polling), `:264` (< 3 s). O teste da correção de lease do ciclo 1 é de timing.
Impacto: flakiness em CI lento; o teste de regressão do lease pode passar ou falhar por acaso.
Correção sugerida: relógio injetável em `jobs`/heartbeat e eventos em vez de sleeps.

### MEDIUM — Concorrência real de claim (SKIP LOCKED) só testada em escala mínima
Dimensões: testes (T7) · Status: confirmado (auditor)
Evidência: testes de claim com 2 threads e 1 job; nenhum teste com N workers × M jobs no Postgres.
Impacto: duplicidade ou perda de jobs sob carga só aparece em produção.
Correção sugerida: teste Postgres com 8 threads × 50 jobs conferindo que cada job foi executado exatamente uma vez.

### MEDIUM — Web só tem testes de funções puras
Dimensões: testes (T2) · Status: confirmado (auditor)
Evidência: 8 arquivos de teste em `apps/web/src/lib`; testing-library e jsdom instalados sem uso; nenhum componente, página ou route handler testado (inclusive o proxy de S1-S3). A remoção dos `loading.tsx` regrediu sem teste.
Impacto: o proxy (o ponto sensível de segurança do web) e as páginas não têm rede de proteção.
Correção sugerida: começar pelo route handler do proxy (montagem de caminho, cabeçalhos, corpo) e pelas páginas de experimento e trace.

### LOW — [WIP] Ruff: 4 erros triviais
Dimensões: testes (verificação do orquestrador) · Status: confirmado
Evidência: `server/src/matrix_server/storage/models.py:427` (ordem de imports, `noqa` inútil); `server/tests/test_run_store.py:167,183`.
Impacto: CI vermelho no lint quando o WIP for commitado.
Correção sugerida: `uv run ruff check --fix .` e revisar.

### LOW — [WIP] Dois testes de `test_runs_api.py` mandam float NaN/inf em vez das strings do contrato
Dimensões: testes (verificação do orquestrador) · Status: confirmado
Evidência: `server/tests/test_runs_api.py` envia `float('nan')`/`inf` com `json=` e o httpx recusa; o contrato pede `"nan"`/`"inf"`.
Impacto: falha falsa que esconde a verificação real do contrato.
Correção sugerida: enviar as strings do contrato (ou `content=` com JSON montado à mão, se o objetivo for testar a recusa).

### LOW — CI sem `permissions:` e actions fixadas por tag
Dimensões: segurança (S7) · Status: confirmado (leitura pelo lead)
Evidência: `.github/workflows/ci.yml:1-7` sem `permissions`; `actions/checkout@v4`, `astral-sh/setup-uv@v5`, `actions/setup-node@v4`.
Impacto: token do workflow com permissões padrão; uma tag movida por terceiros executa código no CI.
Correção sugerida: `permissions: contents: read` e actions fixadas por SHA.

### LOW — Online eval termina sem gravar score quando `value=None`
Dimensões: falhas silenciosas (F6) · Status: confirmado em parte (o lead refutou a outra metade)
Evidência: `server/src/matrix_server/workers/online_handlers.py:194-195` (`if result.value is None: return  # not applicable`). A parte do "payload malformado sem log" foi refutada: `:107-109` loga `warning`. Rebaixado de MEDIUM por isso e porque o retorno é comportamento intencional comentado.
Impacto: avaliações online não aplicáveis (ex.: span sem contexto) não deixam rastro, e o usuário não sabe por que não há nota.
Correção sugerida: contador por avaliação (`skipped_not_applicable`) visível na UI, ou log `debug`.

### LOW — `log_artifact` devolve `None` em falha e não loga 5xx/429 nem o elo órfão
Dimensões: falhas silenciosas (F9) · Status: confirmado (auditor)
Evidência: `sdk/python/src/matrix_sdk/tracking.py:376-395`.
Impacto: artefato perdido sem mensagem quando o retry se esgota.
Correção sugerida: logar `error` no fim dos retries e quando o upload deu certo mas o link ao run falhou.

### LOW — OTLP: spans rejeitados só vão para `partial_success`
Dimensões: falhas silenciosas (F10) · Status: confirmado (auditor)
Evidência: `server/src/matrix_server/api/ingest.py:119-133`; `ingest/openinference.py:546-551`.
Impacto: o operador não vê rejeições no servidor; só um cliente OTLP atento vê.
Correção sugerida: log agregado e contador de spans rejeitados por motivo.

### LOW — Web: matriz do experimento vazia se o dataset foi apagado
Dimensões: falhas silenciosas (F11) · Status: confirmado (auditor)
Evidência: `apps/web/src/app/experiments/[experimentId]/page.tsx:375-387`.
Impacto: resultados existentes ficam invisíveis, sem explicação.
Correção sugerida: renderizar a partir dos `results` (com `item_id`) e mostrar "dataset removido".

### LOW — `log_artifact` de diretório segue symlinks e inclui `.env` e arquivos ocultos
Dimensões: segurança (S8) · Status: confirmado (reproduzido pelo auditor)
Evidência: `sdk/python/src/matrix_sdk/tracking.py:378-379`; um `link.txt` apontando para fora do diretório entrou no zip.
Impacto: vazamento acidental de segredos ou arquivos fora da pasta.
Correção sugerida: não seguir symlinks que saiam da raiz; excluir `.env*` e ocultos por padrão, com opção explícita para incluir.

### LOW — API sem configuração de logs; sem documentação de backup e restauração
Dimensões: produção (P9) · Status: confirmado (auditor)
Evidência: só o worker chama `logging.basicConfig`; nenhum documento de backup de Postgres e ClickHouse.
Impacto: os `log.warning` da API (ex.: célula falhou, ganchos) podem não aparecer; recuperação sem roteiro.
Correção sugerida: configurar logging no `matrix-api` (nível por variável); seção de backup no README.

### LOW — Contêineres como root, imagens mutáveis, sem limites/restart e sem `.dockerignore` no servidor
Dimensões: segurança (S6), produção (P7, P11 em parte) · Status: confirmado (leitura pelo lead)
Evidência: `server/Dockerfile:2` (`ghcr.io/astral-sh/uv:latest`); `deploy/compose.yaml` sem `user`, `restart`, limites ou `stop_grace_period`; `server/` não tem `.dockerignore` (o `apps/web/.dockerignore` existe, então essa parte do P7 vale só para o servidor); imagens com tag de série (`clickhouse-server:24.8`, `pgvector:pg16`).
Impacto: builds não reprodutíveis; contexto de build do servidor pode levar `.venv` ou segredos locais; escalada facilitada se um contêiner for comprometido.
Correção sugerida: fixar imagens por digest, `USER` sem privilégio, `.dockerignore` no servidor, `restart: unless-stopped` e limites.

### LOW — Migração no comando da API e CI sem downgrade, docker build ou validação do compose
Dimensões: produção (P8) · Status: confirmado (leitura pelo lead)
Evidência: `deploy/compose.yaml:45` (`alembic upgrade head && exec matrix-api` em cada réplica); `.github/workflows/ci.yml` sem `alembic downgrade`, `docker build` ou `docker compose config`; web sem lint.
Impacto: corrida de migração com várias réplicas; o compose quebrado (achado HIGH do MinIO) passou pelo CI.
Correção sugerida: serviço `migrate` de execução única com `depends_on: service_completed_successfully`; passos de compose e build no CI.

### LOW — Versões soltas no servidor e no SDK
Dimensões: produção (P11) · Status: confirmado (auditor)
Evidência: `server/pyproject.toml` sem faixas (atenuado por `uv.lock` commitado e `--frozen`); extras do SDK sem versão.
Impacto: usuários do SDK podem resolver versões incompatíveis de torch, transformers e similares.
Correção sugerida: faixas mínimas e máximas nos extras do SDK.

### LOW — Vários arquivos de teste fazem `drop_all`/`create_all` no mesmo `matrix_test`
Dimensões: testes (T8) · Status: confirmado (auditor)
Evidência: fixtures de Postgres em vários arquivos; hoje a execução é serial, sem xdist.
Impacto: quebra assim que alguém ligar paralelismo.
Correção sugerida: schema ou banco por worker de teste.

### LOW — Asserts fracos e testes só com mock nos providers
Dimensões: testes (T9) · Status: confirmado (auditor)
Evidência: providers testados com `MockTransport`, sem fixture de resposta real gravada; `body["git"]["commit"]` só checa formato.
Impacto: mudança de formato do provedor passa despercebida.
Correção sugerida: fixtures com respostas reais anonimizadas de cada provider.

### LOW — Sem teste de corpo chunked sem Content-Length acima do limite
Dimensões: testes (T10) · Status: confirmado (auditor)
Evidência: o limite de corpo (`limits.py`) é coberto por Content-Length; não há caso chunked acima do limite.
Impacto: regressão no caminho de streaming passaria sem ser vista.
Correção sugerida: teste com gerador de corpo chunked acima de `MATRIX_MAX_JSON_BODY_BYTES` esperando 413.

## Verificado e correto (resumo dos auditores)

- Segurança: as 79 rotas montadas declaram `require`/`require_user`/`require_admin` (só login e `/health` sem dependência, por design); argon2id com hash fictício; SHA-256 + `compare_digest`; token admin vazio → 403; o egress bloqueia faixas, valida todos os IPs, fixa o IP e revalida redirects; artifact store sem nome de arquivo no caminho, inline só raster com CSP sandbox; runs e artefatos filtrados por `project_id`; ClickHouse parametrizado; o SDK tira credenciais do remote git e não lê variáveis de ambiente.
- Produção: healthcheck da API com `urllib`; web standalone com variáveis em runtime; worker com SIGTERM gracioso, lease e SKIP LOCKED; um único head do Alembic (0002) e `alembic check` no CI.
- Testes: o CI define as variáveis lidas pelos testes; dedup de retry, amplificação de resource e ReDoS têm testes que falhariam sem a correção.

## Observações fora do escopo

- O `README.md` descreve o MinIO como parte da stack, mas nada o usa na HEAD (relacionado ao HIGH do MinIO; registrado só para atualizar a documentação junto).
- As dimensões performance, fluxos de interface, apps com LLM e comentários não foram auditadas. Os `loading.tsx` removidos (report do ciclo 1, risco 2) ficam pendentes para fluxos de interface.

## Backlog
- fix · small — tirar do `deploy/compose.yaml` o serviço `minio` (imagem `minio/minio` não baixa) até o S3 do ciclo 2, e validar o compose no CI (HIGH, confirmado)
- fix · small — publicar o web só em `127.0.0.1:3000` e pôr no README o aviso "web sem login, não exponha fora de rede privada" até o login/RBAC do G0 (HIGH, confirmado)
- fix · small — experimento com todas as células falhando vira `failed`; expor taxa de erro por variante e tratar baseline com valor vs atual `None` como regressão/indeterminado em `evals/runner.py` (HIGH, confirmado)
- fix · medium — gravar o juiz efetivo no score (`source="heuristic"` quando `HeuristicJudge`), avisar na partida do worker e repassar `MATRIX_JUDGE_*`/chaves no compose (HIGH, confirmado)
- fix · small — `test_start_run_body` com repositório git temporário em vez de depender do clone (MEDIUM, confirmado)
- fix · small — [WIP] egress: remover `x-api-key`, `api-key` e `x-goog-api-key` (ou usar lista de permissão) em redirect cross-origin, `egress/client.py:40` (MEDIUM, confirmado)
- fix · small — compose: tirar portas públicas de ClickHouse e MinIO e exigir senhas por variável sem padrão (MEDIUM, confirmado)
- fix · small — `/ready` checando Postgres e span store; healthcheck de worker e web (MEDIUM, confirmado)
- fix · small — `init_db`/`create_all` só em dev; em Postgres exigir `alembic_version` no head na partida (MEDIUM, confirmado)
- fix · small — SDK: distinguir `sys.exit(código≠0)` de término normal no `_atexit` (`tracking.py:473`) (MEDIUM, confirmado)
- fix · small — CI falha se houver testes pulados nas suítes de serviço (`MATRIX_REQUIRE_SERVICES=1` ou `skipped == 0`) (MEDIUM, confirmado)
- fix · small — `MATRIX_SPAN_TTL_DAYS` no `Settings`, repassado em `build_span_store`, com `MODIFY TTL` em tabela existente (MEDIUM, confirmado)
- fix · small — [WIP] investigar contagem 10001 vs 10000 no reenvio de métricas (T26) (MEDIUM, não confirmado)
- fix · small — proxy web: recusar segmentos `.`/`..` e garantir `pathname` sob `/api/v1/` (MEDIUM, não confirmado)
- fix · small — proxy web: corpo em stream com limite e repasse de nosniff/CSP/cache-control (MEDIUM, confirmado)
- fix · small — README "Primeiro uso" (admin token → projeto → `MATRIX_API_KEY` do web) e web falhando com mensagem clara sem chave (MEDIUM, confirmado)
- fix · small — regras: logar e registrar `skipped_reason` quando a ação é pulada, sem contar como executada (MEDIUM, confirmado)
- fix · small — hook final de falha com fallback que marca o experimento `failed` e loga o erro do hook (MEDIUM, confirmado)
- fix · medium — SDK `finish()`: avisar ops descartadas no prazo e enviar o status mesmo assim (MEDIUM, confirmado)
- refactor · medium — ganchos de ingestão via outbox na fila `jobs`, com contador de falhas (MEDIUM, confirmado)
- fix · medium — [WIP] throttle de login por e-mail global e IP confiável, com semáforo de argon2 (MEDIUM, confirmado)
- fix · medium — [WIP] presigned PUT com tamanho assinado (`content-length-range`) e conferência no `complete` (MEDIUM, confirmado)
- fix · medium — [WIP] verificar SHA-256 em todos os tamanhos e não deduplicar contra objeto não verificado (MEDIUM, confirmado)
- fix · medium — recusar memory/SQLite fora de `MATRIX_ENV=dev` e worker com `span_store=memory` (MEDIUM, confirmado)
- fix · medium — job de CI rodando `make demo` com Postgres e ClickHouse (MEDIUM, confirmado)
- refactor · medium — relógio injetável nos testes de lease e heartbeat em vez de sleeps (MEDIUM, confirmado)
- fix · medium — teste Postgres de claim com 8 threads × 50 jobs, execução exatamente uma vez (MEDIUM, confirmado)
- fix · large — testes de componente/route handler no web, começando pelo proxy e pelas páginas de experimento e trace (MEDIUM, confirmado)
- fix · small — [WIP] `ruff check --fix` em `storage/models.py:427` e `tests/test_run_store.py:167,183` (LOW, confirmado)
- fix · small — [WIP] testes de NaN/inf em `test_runs_api.py` enviando as strings do contrato (LOW, confirmado)
- fix · small — CI com `permissions: contents: read` e actions por SHA (LOW, confirmado)
- fix · small — contador `skipped_not_applicable` nas avaliações online (LOW, confirmado)
- fix · small — `log_artifact` loga falha final e elo órfão (LOW, confirmado)
- fix · small — log e contador de spans OTLP rejeitados (LOW, confirmado)
- fix · small — página de experimento renderiza resultados mesmo com dataset apagado (LOW, confirmado)
- fix · small — `log_artifact` de diretório sem seguir symlinks para fora e sem `.env`/ocultos por padrão (LOW, confirmado)
- fix · small — logging configurado no `matrix-api` e seção de backup no README (LOW, confirmado)
- fix · small — imagens por digest, `USER` sem privilégio, `.dockerignore` no servidor, restart e limites no compose (LOW, confirmado)
- fix · small — faixas de versão nos extras do SDK (LOW, confirmado)
- fix · small — teste de corpo chunked acima do limite esperando 413 (LOW, confirmado)
- refactor · medium — serviço `migrate` único no compose e CI com downgrade, docker build e `compose config` (LOW, confirmado)
- refactor · medium — banco/schema por worker de teste no Postgres (LOW, confirmado)
- fix · medium — fixtures de respostas reais de provider nos testes (LOW, confirmado)
