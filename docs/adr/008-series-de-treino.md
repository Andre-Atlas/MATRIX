# ADR-008: séries temporais de treino no ClickHouse com downsampling M4

Status: aceito · 2026-11-10

## Contexto
Um treino gera de milhares a dezenas de milhões de pontos (loss por passo, gradientes, telemetria de hardware). A UI precisa desenhar curvas de vários runs sem transferir todos os pontos.

## Decisão
- **Metadados do run** (params, git, ambiente, status, tags) em PostgreSQL; **pontos de métrica** em ClickHouse: `run_metrics(project_id, run_id, key, step, ts, value, version)` em `ReplacingMergeTree(version)` ordenada por `(project_id, run_id, key, step)`. Reenvio do mesmo `(run, key, step)` substitui (idempotência para retries do SDK; a última escrita vence). Leituras sempre deduplicam (`LIMIT 1 BY`).
- **Downsampling M4 na própria consulta** (em vez de LTTB em Python): o eixo de passos é dividido em `max_points/4` faixas e cada faixa devolve primeiro, último, mínimo e máximo. Preserva picos e vales, é exato para gráficos de linha e não exige trazer os pontos brutos para a API. `max_points` padrão 1000 e máximo 5000.
- Telemetria de hardware usa o mesmo caminho com chaves `sys/...`; o passo é o segundo desde o início do run.
- Valores `NaN`/`±Inf` são aceitos (enviados como as strings `"nan"`, `"inf"`, `"-inf"`) e devolvidos como `null` no JSON.
- Parâmetros do run são imutáveis depois de definidos (mesmo valor é idempotente; valor diferente dá `409`), como no MLflow, para garantir reprodutibilidade.
- Runs `running` sem batimento por 5 minutos viram `failed` ("lost heartbeat") por um job periódico.

## Consequências
- O LTTB previsto na spec do ciclo 2 foi substituído por M4 (ruling no ledger).
- A API de leitura devolve séries já reduzidas; o front não precisa decimar.
