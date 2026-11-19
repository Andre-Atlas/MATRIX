# MATRIX — Spec do ciclo 2: plataforma completa

```
ATLAS · feature · arquitetura+dados+infra+design+produto+auditoria · large → pesquisa, descoberta, plano + ADR, G1, SDD (despacho via atlas-dispatch), revisão + segurança por fatia, verificação, G2 · lanes: design, arquitetura, auditoria
```

Origem: [docs/blueprint-sota.md](../../blueprint-sota.md). O ciclo 1 ([spec](../2026-11-09-plataforma-mvp/spec.md), [relatório](../2026-11-09-plataforma-mvp/report.md)) entregou a fatia LLMOps. Este ciclo entrega **todo o resto do blueprint**.

## 1. O que falta do blueprint

| Blueprint | Estado no ciclo 1 | Este ciclo |
|---|---|---|
| Módulo 1 — Model, Data & Prompt Hub | só Prompt CMS | **G3** Model & LoRA Registry, versionamento de datasets, linhagem DAG |
| Módulo 2 — Training & Fine-Tuning Studio | nada | **G1** tracking de treino + telemetria; **G2** sweeps; avaliação de ML clássico |
| Módulo 3 — Trace & Agent Inspector | feito, sem mídia rica | **G9** PDF, áudio, bounding boxes |
| Módulo 4 — Eval Matrix, RAG Triad & Red-Teaming | matriz e tríade | **G5** scanner OWASP; **G9** métricas de agente e conversa, dataset sintético, comparação cega |
| Módulo 5 — Vector Space & Drift Observatory | nada | **G4** projetor UMAP 3D, clusters, lacunas de conhecimento, drift tabular/texto/embedding |
| Módulo 6 — Human-in-the-loop | feito | **G9** automações (webhook/Slack) |
| Camada 2 — AI Gateway | nada | **G8** proxy compatível com OpenAI |
| Camada 4 — Inspect AI Runner | nada | **G6** avaliação de agentes em sandbox Docker |
| Camada 4 — LM-Eval & VLMEvalKit Runner | nada | **G7** benchmarks fundacionais e leaderboard |
| Pendência do ciclo 1 | UI sem login | **G0** usuários, sessão e RBAC; artifact store |

## 2. Fatias

### G0 — Fundação do ciclo
- **Usuários e RBAC.** Usuários com senha (argon2), sessão por cookie HttpOnly assinado na web, papéis por projeto `owner | admin | member | viewer`. Chaves de API continuam para SDK/ingestão e ganham escopo (`ingest`, `read`, `write`). Toda rota declara o papel mínimo. Troca de projeto na UI.
- **Artifact store.** Interface `ArtifactStore` com backend S3/MinIO e backend de arquivo local (dev/testes). Endereçado por conteúdo (SHA-256), deduplicado, upload/download por URL pré-assinada, limites de tamanho por projeto. Usado por modelos, datasets, mídia de traces e relatórios.
- Voltar com os estados de carregamento da web (investigar a causa no Next ou atualizar).

### G1 — Training & Fine-Tuning Studio (tracking)
- **Runs:** nome, status, params, tags, commit git, ambiente (`requirements`), dataset de entrada (versão), host.
- **Métricas** como séries temporais no ClickHouse (`run_id, key, step, ts, value`), ingestão em lote de alta frequência, downsampling na leitura (LTTB) para gráficos com milhões de pontos.
- **Telemetria de hardware** pelo SDK: GPU (VRAM, utilização, temperatura, potência via NVML quando existir), CPU, RAM, disco, rede.
- **SDK** `matrix_sdk.tracking`: `start_run`, `log_params`, `log_metric(s)`, `log_artifact`, `log_table`; integrações: callback do Hugging Face `Trainer`/TRL (loss, perplexidade, gradiente, margem de recompensa DPO), wrapper genérico de PyTorch, autolog de scikit-learn e XGBoost/LightGBM.
- **Avaliação de ML clássico:** o SDK calcula e registra ROC-AUC, curva PR, matriz de confusão, RMSE/MAE/R², importância de features (SHAP quando instalado; permutação caso contrário). A UI renderiza.
- **UI:** tabela de runs com filtros, comparação de N runs (curvas sobrepostas, suavização, escala log), painel de hardware, tabelas (texto com diff, imagem, áudio).

### G2 — Sweeps
- Controlador no servidor: grid, random, bayesiano (TPE), com poda precoce Hyperband/ASHA. O agente do SDK (`sweep.agent(fn)`) pede a próxima configuração e reporta métricas; a poda responde "pare".
- UI: coordenadas paralelas, importância de hiperparâmetros (floresta aleatória), melhor run.

### G3 — Registry, dados e linhagem
- **Model & LoRA Registry:** modelo → versões imutáveis (artefatos no store, `flavor`: sklearn/pickle, onnx, safetensors, adaptador LoRA com modelo base), assinatura de entrada/saída, aliases móveis (`@champion`, `@challenger`), estágios (`staging`, `production`, `archived`), notas, tags, ligação com o run de origem. Promoção exige papel `admin` e dispara webhook assinado (CI/CD).
- **Versionamento de datasets:** dataset de arquivos = manifesto (caminho → hash, tamanho), versões imutáveis com pai, diff entre versões, deduplicação por conteúdo. Os golden datasets do ciclo 1 ganham versões (snapshot) usadas pelos experimentos. Adaptadores opcionais para apontar para repositórios LakeFS e arquivos `.dvc` (ADR).
- **Linhagem:** grafo `dataset version → run → model version → experimento/benchmark`, consultável e desenhado com React Flow.

### G4 — Vector Space & Drift Observatory
- **Embeddings:** tabela no ClickHouse (`project, kind: query|document|output, ref, model, vector`). Fontes: vetores enviados pelo SDK/instrumentação (hoje descartados na ingestão) e job que embeda entradas de traces e chunks recuperados.
- **Projeção:** job UMAP 2D/3D (PCA como fallback), clusters HDBSCAN, métrica por cluster (média de scores, taxa de erro), **lacunas de conhecimento** (clusters de perguntas longe dos documentos).
- **Drift:** referência × atual para (a) features tabulares — KS, PSI, Wasserstein, KL, JS, χ², escolhidos pelo tipo e volume; (b) texto — comprimento, tokens, OOV, legibilidade, regex proibida, sentimento; (c) embeddings — distância de centroides, JS sobre a projeção, classificador de domínio. Qualidade de dados: nulos, violação de schema, correlações, outliers.
- **Monitores** agendados (fila de jobs) que geram alertas pelas regras existentes.
- **UI:** projetor 3D em WebGL (deck.gl), cor por cluster/score/origem, seleção → lista de traces → dataset; painel de drift por feature com histórico.

### G5 — Red-teaming (OWASP Top 10 para LLMs)
- **Biblioteca de ataques** por categoria: prompt injection direta e indireta (via documento do RAG), jailbreak (persona, Base64, ROT13, leetspeak, multi-turno crescendo), vazamento de PII e de system prompt, BOLA/BFLA via agente, injeção SQL/shell em argumentos de ferramenta, alucinação, viés, toxicidade.
- **Alvos:** endpoint HTTP com requisição modelada, endpoint compatível com OpenAI, ou prompt + modelo do CMS.
- **Detectores:** canário, regex/PII, recusa, juiz LLM. **Relatório** por categoria com taxa de sucesso do ataque, evidência por caso, e "achado → caso de regressão".
- **Segurança do próprio scanner:** alvo HTTP é SSRF por definição — bloqueio de faixas privadas e metadados por padrão, lista de hosts permitidos por projeto, só papel `admin` cria alvo.

### G6 — Avaliação de agentes em sandbox
- Modelo `Dataset → Solver → Scorer`. Um container Docker por amostra (sem rede por padrão, limites de CPU/memória/tempo, usuário sem privilégio).
- Solvers: ReAct com ferramentas `bash`, `python`, arquivos; scorers: arquivo existe, comando sai com 0 (teste unitário no sandbox), contém, juiz.
- Transcrição completa vira trace do MATRIX (reaproveita o inspector).
- Execução por um serviço **runner** separado da API e do worker (é o único com acesso ao Docker).

### G7 — Benchmarks fundacionais
- Jobs de benchmark: runner dispara `lm-eval-harness` (texto) e `VLMEvalKit` (multimodal) contra um endpoint compatível com OpenAI (vLLM, SGLang) ou um modelo do registry em GPU, e ingere os resultados.
- **Leaderboard:** versão de modelo × tarefa × métrica, comparação entre versões (base × fine-tune), ligação com o registry e a linhagem.

### G8 — AI Gateway
- `POST /gateway/v1/chat/completions` e `/embeddings` compatíveis com OpenAI, com streaming.
- Chaves virtuais por projeto; aliases de modelo → rotas; fallback entre provedores; retries; rate limit (requisições e tokens por minuto) e orçamento por chave; cache exato e semântico; divisão de tráfego A/B; contagem de custo; **trace automático** de cada chamada no MATRIX (com TTFT).
- Segredos de provedor nunca no banco: a rota referencia o **nome** de uma variável de ambiente.

### G9 — Eval+ e acabamento
- Métricas novas: context recall, context entities recall, noise sensitivity, factual correctness (F1/Fβ), tool call accuracy, agent goal accuracy, knowledge retention, role adherence, conversation completeness, métrica DAG determinística.
- Gerador de dataset sintético com evoluções (raciocínio, multi-contexto, condicional).
- Comparação cega par a par entre dois experimentos.
- Inspector: páginas de PDF, player de áudio, bounding boxes sobre imagem.
- Automações: ação de regra `webhook` (Slack/PagerDuty), com assinatura.

## 3. Decisões de arquitetura (viram ADRs 006–012 na primeira tarefa de cada fatia)

| ADR | Decisão proposta |
|---|---|
| 006 | Sessão por cookie assinado + RBAC por projeto; chaves de API com escopo |
| 007 | Artifact store endereçado por conteúdo sobre S3/MinIO; LakeFS e DVC como adaptadores, não dependência |
| 008 | Séries temporais de treino no ClickHouse, downsampling LTTB na leitura |
| 009 | Estatística de drift e UMAP/HDBSCAN implementados com numpy/scipy/scikit-learn/umap-learn num extra `analytics` do worker (mesma linha do ADR-005) |
| 010 | **Inspect AI e lm-eval-harness/VLMEvalKit usados como motores reais**, dentro de um serviço runner isolado, com resultados normalizados para o MATRIX (benchmark só vale se for a implementação padrão) |
| 011 | Gateway no mesmo pacote, processo próprio (`matrix-gateway`); segredos só por referência a variável de ambiente |
| 012 | Política de saída (egress) única para red-team, webhooks e gateway: bloqueio de redes privadas + lista de hosts permitidos |

## 4. Limites deste ambiente (o que eu não consigo validar aqui)

- **Sem GPU.** G7 e a telemetria de GPU do G1 são testados em CPU, com modelos mínimos e NVML simulado. A execução real em GPU fica para validar no seu hardware.
- **Sem chaves de provedor.** Juízes, gateway e red-team são testados com o provider `fake` e servidores HTTP locais.
- **Docker disponível** nesta sessão: sandboxes (G6) e `docker compose` serão executados de verdade.

## 5. Critério de pronto do ciclo

Cada fatia tem um roteiro em `scripts/verify_<fatia>.py` (como o `make demo` do ciclo 1) que passa contra ClickHouse, PostgreSQL e MinIO reais:

1. **G0** login, papel `viewer` não escreve, upload/download de artefato deduplicado.
2. **G1** um treino real de scikit-learn e um laço PyTorch em CPU aparecem com curvas, hardware e relatório de classificação.
3. **G2** sweep TPE com ASHA converge numa função conhecida e poda runs ruins.
4. **G3** modelo treinado no item 2 registrado, promovido a `@champion` com webhook recebido, linhagem dataset → run → modelo visível.
5. **G4** projeção 3D de embeddings de um conjunto com tópicos conhecidos separa os clusters; drift injetado numa feature é detectado e gera alerta.
6. **G5** scanner encontra as vulnerabilidades plantadas num alvo de teste e não acusa o alvo seguro.
7. **G6** agente resolve uma tarefa de arquivo dentro de um container e o scorer confere o resultado; a transcrição aparece como trace.
8. **G7** benchmark pequeno roda de ponta a ponta e entra no leaderboard.
9. **G8** chamada pelo gateway com fallback, cache e rate limit gera trace com custo.
10. **G9** métricas novas e comparação cega funcionando na matriz.
11. `make test` verde, `docker compose up` sobe a stack completa, CI verde.
