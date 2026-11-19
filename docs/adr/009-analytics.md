# ADR-009: análise numérica (UMAP, HDBSCAN, drift) como extra `analytics` do worker

Status: aceito · 2026-11-10

## Decisão
Estatística de drift, projeção UMAP/PCA e clusterização HDBSCAN rodam **no worker**, não na API, com numpy, scipy, scikit-learn, `umap-learn` e `hdbscan` instalados pelo extra `analytics` (a imagem do worker o inclui; a API não). A API devolve resultados já calculados (coordenadas, rótulos de cluster, estatísticas). Sem o extra, os jobs falham com mensagem clara e a UI mostra "análise indisponível".
Testes de drift: implementações comparadas com `scipy.stats` (KS, χ², Wasserstein) e valores conhecidos (PSI, KL, JS) calculados à mão.
Escala: projeção limitada a 50 000 pontos por job (amostragem estratificada por cluster/origem); UMAP em CPU leva segundos a minutos nessa faixa.

## Consequências
- Imagens separadas (api leve, worker pesado) e dependências nativas isoladas do caminho de ingestão.
- A mesma fila de jobs (ADR-003); jobs longos usam heartbeat de lease (correção do ciclo 1).
