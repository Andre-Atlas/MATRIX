# Mapeamento do Estado da Arte (SOTA): Frameworks de Análise, Teste, Avaliação e Versionamento (ML, LLM, RAG & Agentes)

Este documento cataloga exaustivamente todas as funcionalidades (features) dos principais frameworks do mercado para **Machine Learning Clássico (MLOps)**, **Modelos de Linguagem de Grande Porte (LLMOps)**, **RAG Avançado** e **Sistemas Multiagentes/Multimodais (AgentOps)**. O objetivo final deste mapeamento é servir como **Especificação Funcional e Arquitetural (Blueprint)** para o desenvolvimento de uma plataforma única, dinâmica e de alta observabilidade.

---

## 1. Versionamento de Experimentos, Modelos e Dados (ML Clássico & Fine-Tuning SFT/DPO)

Estes frameworks governam o ciclo de vida do treinamento, desde a ingestão de dados brutos e ajuste de hiperparâmetros até o registro de pesos neurais (checkpoints/LoRA) e modelos tabulares.

### 1.1. Weights & Biases (W&B Core + W&B Weave)
Focado em rastreamento profundo de treinamento de redes neurais, ajuste fino de LLMs e avaliação de aplicações generativas.
*   **W&B Experiments (Tracking de Treino):**
    *   Coleta em tempo real de séries temporais de métricas de treino ($\text{Loss}$, Perplexidade $\text{PPL} = \exp(\mathcal{L})$, acurácia, gradientes e pesos das camadas).
    *   Telemetria de hardware automática (uso de VRAM da GPU, temperatura, consumo de energia em Watts, I/O de disco, utilização de rede e CPU).
    *   Integração zero-code com PyTorch, Hugging Face `Trainer`, DeepSpeed, Megatron-LM, Axolotl, Unsloth e TRL (para monitoramento de margens de recompensa em DPO/PPO/GRPO).
*   **W&B Artifacts (Linhagem e Versionamento DAG):**
    *   Versionamento imutável de datasets, modelos e resultados intermediários com verificação por hash criptográfico (MD5/SHA-256).
    *   Grafo Acíclico Direcionado (DAG) visual que mostra exatamente qual versão de dataset gerou qual checkpoint de modelo e qual execução de avaliação consumiu esse modelo.
    *   Deduplicação automática de blocos de armazenamento em nuvem (S3/GCS/Azure Blob).
*   **W&B Sweeps (Otimização de Hiperparâmetros):**
    *   Busca automatizada via Grid Search, Random Search e Otimização Bayesiana (Processos Gaussianos / Tree-structured Parzen Estimators).
    *   Algoritmos de poda precoce (*Early Stopping*) como **Hyperband** e **ASHA** para interromper treinos pouco promissores e economizar GPU.
    *   Gráficos de Coordenadas Paralelas (*Parallel Coordinates Plot*) e importância de hiperparâmetros via Random Forest interna.
*   **W&B Tables (Inspeção de Dados Multimodais):**
    *   Visualizador tabular rico no navegador com suporte a tensores de imagem, máscaras de segmentação, bounding boxes interativas, espectrogramas de áudio, vídeos, moléculas 3D e textos com *diff* visual.
*   **W&B Model Registry (Governança):**
    *   Controle de estágios de modelo (`Staging`, `Production`, `Archived`), webhooks de CI/CD disparados na promoção de modelos e controle de acesso baseado em função (RBAC).
*   **W&B Weave (Módulo LLMOps):**
    *   Decorador `@weave.op()` para rastrear entradas, saídas, código-fonte da função e latência automaticamente.
    *   Sistema de avaliação comparativa com gráficos de radar (*Radar Plots*) cruzando latência, custo em USD e acurácia de múltiplos modelos.

---

### 1.2. MLflow (v2.x / v3.x)
O padrão open-source agnóstico de nuvem para governança ponta a ponta de Machine Learning tradicional e IA Generativa.
*   **MLflow Tracking:**
    *   API unificada para registrar parâmetros (`log_param`), métricas (`log_metric`), artefatos (`log_artifact`), código-fonte (commit hash do Git), ambiente virtual (`conda.yaml`, `requirements.txt`) e assinaturas de entrada/saída (*Model Signatures*).
    *   *Autologging* nativo para Scikit-Learn, XGBoost, LightGBM, CatBoost, Spark MLlib, PyTorch Lightning, TensorFlow/Keras e Transformers.
*   **MLflow Model Format ("Flavors"):**
    *   Empacotamento padronizado que permite carregar o mesmo modelo como função Python genérica (`pyfunc`), container Docker, endpoint REST ou função UDF do Apache Spark.
*   **MLflow Model Registry:**
    *   Versionamento centralizado com suporte a *Aliases* mutáveis (ex.: `@champion`, `@challenger`), anotações em Markdown, tags de validação e linhagem direta com a *Run* de treinamento.
*   **MLflow Evaluate:**
    *   Avaliação automatizada de modelos clássicos (curvas ROC-AUC, matriz de confusão, curvas Precision-Recall, gráficos SHAP de explicabilidade feature-a-feature, métricas de regressão como $\text{RMSE}$, $\text{MAE}$ e $R^2$).
    *   Avaliação de LLMs com juízes configuráveis e métricas estáticas de texto.
*   **MLflow Tracing & AI Gateway:**
    *   Instrumentação compatível com OpenTelemetry para capturar chamadas de LLMs e agentes.
    *   Proxy centralizado (Gateway) para gerenciamento unificado de chaves de API, *rate limiting* (limites de requisição/minuto), *fallback* automático entre provedores e roteamento de tráfego A/B.

---

### 1.3. DVC (Data Version Control) & LakeFS
Sistemas focados no versionamento pesado de dados (terabytes/petabytes) e reprodutibilidade determinística de pipelines.
*   **Versionamento estilo Git para Dados:**
    *   Armazena arquivos pesados (Parquet, imagens, áudio, pesos `.safetensors`) em Object Storage (S3, MinIO, GCS) e mantém apenas ponteiros leves (`.dvc`) no repositório Git.
*   **Pipelines Determinísticos (`dvc.yaml` e `dvc.lock`):**
    *   Criação de grafos de execução onde cada etapa declara suas dependências (`deps`), parâmetros (`params.yaml`), comandos e saídas (`outs`).
    *   Cache inteligente de etapas: `dvc repro` reexecuta apenas os nós do grafo cujos dados ou scripts sofreram alteração no hash.
*   **LakeFS (Zero-Copy Data Branching):**
    *   Operações de `branch`, `commit`, `merge` e `revert` diretamente sobre Data Lakes (S3/Delta Lake/Iceberg) sem duplicar arquivos físicos (*Copy-on-Write*), permitindo testar pipelines em "branches de dados" isoladas.

---

## 2. Observabilidade, Tracing e Gestão de Prompts (LLMOps & AgentOps)

Estes frameworks monitoram o comportamento em tempo real de LLMs, sistemas de RAG e grafos de agentes em desenvolvimento e produção.

### 2.1. Arize Phoenix
Plataforma open-source construída sobre o protocolo universal **OpenInference / OpenTelemetry**, com forte capacidade de análise vetorial.
*   **OpenInference Tracing (Waterfall & Flamegraph):**
    *   Captura hierárquica de *Traces* (fluxo completo) e *Spans* (etapas atômicas: `LLM`, `RETRIEVER`, `RERANKER`, `TOOL`, `AGENT`, `GUARDRAIL`, `CHAIN`).
    *   Inspeção detalhada de payloads: System Prompt, mensagens do histórico, parâmetros de amostragem ($\text{temperature}$, $\text{top\_p}$), ferramentas disponíveis (JSON Schema), tokens de entrada/saída, tokens de raciocínio (*reasoning tokens*) e latência do primeiro token (*TTFT - Time to First Token*).
*   **Análise de Espaço Latente e Embeddings (UMAP Projector):**
    *   Projeção tridimensional interativa (via UMAP ou t-SNE) de milhões de vetores de consultas e chunks do banco vetorial.
    *   Detecção automática de *clusters* (agrupamentos por densidade HDBSCAN) onde a métrica de qualidade é baixa ou onde há **Drift Semântico** (perguntas dos usuários em produção divergindo da distribuição dos documentos indexados).
    *   Cálculo de distância Euclidiana e Divergência de Jensen-Shannon ($D_{\text{JS}}$) entre distribuições de embeddings de referência e produção.
*   **Prompt Playground & Versionamento:**
    *   Editor interativo de prompts com variáveis `{{mustache}}`, suporte a múltiplos provedores simultâneos para comparação lado a lado diretamente sobre spans reais capturados em produção.
    *   Histórico de revisões de prompts com tags de produção.
*   **Datasets & Experimentos:**
    *   Conversão com um clique de spans problemáticos da produção em linhas de um *Golden Dataset*.
    *   Execução de experimentos versionados mostrando o *diff* de métricas entre a versão $v_1$ e $v_2$ do pipeline RAG.

---

### 2.2. Langfuse
Plataforma open-source de engenharia de LLMs focada em rastreabilidade de agentes, gestão de custos, cache de prompts e fluxos de anotação humana.
*   **Hierarquia de Observabilidade:**
    *   Agrupamento em 3 níveis: **Sessions** (conversas multi-turno completas de um chat) $\rightarrow$ **Traces** (uma interação pergunta-resposta) $\rightarrow$ **Observations/Spans/Generations** (chamadas individuais dentro do turno).
*   ** Visualização de Grafo de Agentes:**
    *   Renderização dinâmica da topologia do agente e do fluxo de transição de estados (quem chamou qual subagente e quantas vezes um loop de reflexão rodou).
*   **Prompt Management (CMS de Prompts com Cache):**
    *   Versionamento de prompts de texto e de chat (`system`/`user`/`assistant`) desacoplado do código da aplicação.
    *   Cache local no SDK com atualização em background (zero adição de latência na inferência) e *rollbacks* instantâneos via UI sem necessidade de novo deploy de código.
*   **Filas de Anotação Humana (*Annotation Queues*) e RLHF:**
    *   Interface dedicada para especialistas de domínio revisarem traces, atribuírem notas categóricas, binárias ou contínuas e corrigirem respostas manualmente para gerar datasets de *Fine-Tuning* (SFT/DPO).
*   **Motor de Custos e Analytics:**
    *   Tabela atualizada de preços por token (input, output, cache read, cache write, áudio, imagem) para centenas de modelos.
    *   Dashboards de consumo agregados por `user_id`, `tenant_id`, versão de prompt, ambiente ou tag.
*   **Avaliação Online Assíncrona:**
    *   Execução de avaliadores *LLM-as-a-Judge* em background sobre uma amostragem configurável (ex.: 10% do tráfego de produção) para pontuar *Hallucination* e *Toxicity* em tempo real.

---

### 2.3. LangSmith
Plataforma focada na depuração profunda de cadeias complexas e máquinas de estado de agentes (LangGraph).
*   **Inspeção de Estado em Grafos (Time-Travel Debugging):**
    *   Visualização exata da mutação do dicionário de estado (`State`) a cada nó executado pelo agente.
    *   Capacidade de pausar, editar variáveis de estado intermediárias e reexecutar o fluxo a partir de um ponto específico.
*   **Automations & Rules:**
    *   Regras condicionais automáticas (ex.: *"Se `latency > 5s` E `feedback_score == 0`, envie o trace para a fila de revisão humana, adicione ao dataset `regressao_critica` e dispare alerta no Slack/PagerDuty"*).
*   **Pairwise Evaluation & Canvas:**
    *   Interface visual para comparação cega (*blind A/B test*) entre duas execuções de experimentos diferentes.

---

## 3. Frameworks de Testes Unitários, Métricas de RAG, Agentes e Red-Teaming

Ferramentas focadas em calcular métricas matemáticas, semânticas e comportamentais para aprovação em CI/CD (GitHub Actions / GitLab CI).

### 3.1. Ragas (Retrieval-Augmented Generation Assessment - v0.2+)
Framework especializado na decomposição matemática e proposicional de sistemas de RAG e Agentes.
*   **Métricas de Recuperação (Retriever):**
    *   **Context Precision:** Mede se os chunks mais relevantes para a resposta estão ranqueados no topo da lista recuperada:
        $$\text{Context Precision@K} = \frac{\sum_{k=1}^K \left( \text{Precision@k} \times v_k \right)}{\text{Total de itens relevantes no Top } K}$$
    *   **Context Recall:** Verifica qual fração das afirmações da resposta de referência (*Ground Truth*) pode ser atribuída aos fragmentos recuperados.
    *   **Context Entities Recall:** Mede a proporção de entidades nomeadas (NER: datas, valores, nomes próprios, códigos) preservadas pelo *retriever*.
    *   **Noise Sensitivity:** Quantifica a tendência do modelo de cometer erros quando exposto a chunks irrelevantes misturados aos chunks corretos.
*   **Métricas de Geração (Generator):**
    *   **Faithfulness (Fidelidade / Groundedness):** Extrai todas as proposições atômicas da resposta gerada ($S = \{s_1, s_2, \dots, s_m\}$) e verifica via NLI quantas são suportadas pelo contexto recuperado $C$:
        $$\text{Faithfulness} = \frac{|V|}{|S|}, \quad V = \{s_i \in S \mid C \models s_i\}$$
    *   **Answer Relevancy:** Gera $N$ perguntas reversas artificiais a partir da resposta do modelo e calcula a similaridade média de cosseno entre os embeddings dessas perguntas ($\mathbf{e}_{g_i}$) e o embedding da pergunta original do usuário ($\mathbf{e}_o$):
        $$\text{Answer Relevancy} = \frac{1}{N} \sum_{i=1}^N \frac{\mathbf{e}_o \cdot \mathbf{e}_{g_i}}{\|\mathbf{e}_o\| \|\mathbf{e}_{g_i}\|}$$
    *   **Factual Correctness:** Combina precisão e revocação de fatos atômicos comparando a saída com a referência (expressa em $F_1$ ou $F_\beta$).
*   **Métricas de Sistemas de Agentes e Multimodais:**
    *   **Tool Call Accuracy:** Avalia tanto a sequência de ferramentas invocadas quanto a exatidão exata dos argumentos JSON passados.
    *   **Agent Goal Accuracy:** Verifica se o estado final da conversa ou do sistema atingiu o objetivo do usuário.
    *   **Multimodal Faithfulness & Relevance:** Verifica se a resposta gerada condiz com os elementos visuais presentes nos chunks de imagens recuperados.
*   **Gerador de Datasets Sintéticos Evolucionários:**
    *   Lê documentos brutos e cria automaticamente pares complexos de `(Pergunta, Contexto, Ground Truth)` aplicando evoluções controladas: *Reasoning* (raciocínio dedutivo), *Multi-Context* (exigindo cruzar múltiplos documentos) e *Conditional* (perguntas com restrições hipotéticas).

---

### 3.2. DeepEval
O "Pytest para LLMs": framework de testes unitários com mais de 40 métricas prontas e scanner de vulnerabilidades embutido.
*   **Arquitetura de Métricas Avançadas:**
    *   **G-Eval:** Avaliação baseada em rubricas customizadas com *Chain-of-Thought* e ponderação pelas probabilidades logarítmicas (*logprobs*) dos tokens de nota emitidos pelo modelo juiz:
        $$\text{Score}_{\text{G-Eval}} = \sum_{s=1}^5 s \cdot P(\text{token} = s \mid \text{prompt}, \text{CoT})$$
    *   **DAG (Deep Acyclic Graph Metric):** Avaliador determinístico em formato de árvore de decisão onde cada nó verifica uma condição (por regex, código ou juiz LLM) para atribuir notas sem variância.
    *   **Métricas Conversacionais Multi-Turno:** *Knowledge Retention* (se o bot esquece dados fornecidos turnos atrás), *Role Adherence* (aderência à persona), *Conversation Completeness*.
*   **Motor de Red-Teaming e Segurança (OWASP Top 10 para LLMs):**
    *   Geração automatizada de ataques adversariais contra o sistema em mais de 40 categorias de vulnerabilidade:
        *   *Prompt Injection* (direta e indireta via documentos do RAG).
        *   *Jailbreaking* (DAN, codificação Base64, ROT13, Leetspeak, ataques multi-turno *Crescendo*).
        *   *PII Leakage* (vazamento de CPF, cartões, senhas, chaves de API).
        *   *BFLA / BOLA* (acesso não autorizado a funções ou objetos de outros usuários via agentes).
        *   *Hallucination, Bias, Toxicity, SQL/Shell Injection* via parâmetros de *Tool Calling*.
*   **Integração Nativa com CI/CD:**
    *   Execução paralela via `deepeval test run`, falha de build baseada em *thresholds* por métrica e cache de avaliações em disco.

---

### 3.3. Promptfoo
Ferramenta de linha de comando e interface web matricial para comparação sistemática de prompts, modelos e pentesting de IA.
*   **Matriz Declarativa de Testes:**
    *   Cruza $M$ Prompts $\times$ $N$ Modelos (OpenAI, Anthropic, Gemini, vLLM local, Ollama) $\times$ $K$ Casos de Teste simultaneamente.
*   **Asserções Determinísticas e Estatísticas:**
    *   `equals`, `contains`, `regex`, `is-json` (validação estrita de JSON Schema), `javascript`/`python` (funções customizadas de validação), `cost` (ex.: falhar se a resposta custar mais de $\$0.005$), `latency` (ex.: falhar se demorar mais de $2000\text{ ms}$).
    *   Similaridade léxica (`bleu`, `rouge-n`, `levenshtein`) e vetorial (`similar`).
*   **Visualizador Matricial Web (`promptfoo view`):**
    *   Grade interativa lado a lado com destaque de diferenças (*diff*), taxas de aprovação percentual por coluna e filtros de regressão.

---

### 3.4. Inspect AI (UK AI Safety Institute)
O estado da arte em avaliação de **Agentes Autônomos** que executam código, usam terminais e operam no mundo real.
*   **Ambientes de Execução Isolados (Sandboxing):**
    *   Provisionamento automático de containers Docker ou clusters Kubernetes por caso de teste. O agente pode editar arquivos, rodar comandos `bash`, instalar pacotes, navegar na web e consultar bancos de dados reais sem risco para o host.
*   **Arquitetura Modular (`Dataset` $\rightarrow$ `Solver` $\rightarrow$ `Scorer`):**
    *   Separação limpa entre o ambiente da tarefa, a estratégia de resolução do agente (ReAct, Plan-and-Solve, Multi-agent handoff) e o verificador final (que pode checar se o teste unitário dentro do container passou ou se um arquivo específico foi criado no sistema de arquivos do sandbox).
*   **Inspect View UI:**
    *   Interface web integrada para inspecionar transcrições completas de agentes com dezenas de passos, consumo de tokens e logs de erro de ferramentas.

---

## 4. Motores de Benchmarking de Modelos Fundacionais, Fine-Tuning e VLMs

Utilizados para validar modelos base ou ajustados (SFT, LoRA, QLoRA, DPO) contra benchmarks científicos padronizados.

### 4.1. LM-Evaluation-Harness (EleutherAI) & Lighteval (Hugging Face)
*   **Modos de Avaliação:**
    *   **Log-likelihood (Múltipla Escolha):** Calcula a probabilidade condicional exata $\log P(\text{alternativa} \mid \text{contexto})$ diretamente nos *logits* da camada final do modelo, sem precisar gerar texto, garantindo avaliação rápida e livre de erros de *parsing*.
    *   **Geração Livre e *Few-Shot*:** Suporte a *chat templates* customizados, $N$-shot prompting, extração de respostas via filtros Regex e verificação matemática simbólica (SymPy para benchmarks como MATH e GSM8K).
*   **Aceleração de Inferência:**
    *   Integração nativa com **vLLM**, **SGLang**, **TensorRT-LLM** e `accelerate` (Data Parallelism / Tensor Parallelism em múltiplas GPUs).
*   **Catálogo de Tarefas SOTA:**
    *   Mais de 1.000 benchmarks padronizados: **MMLU-Pro**, **GPQA-Diamond**, **IFEval** (Instruction Following determinístico), **BBH** (BIG-Bench Hard), **HumanEval / MBPP** (código), **TruthfulQA**.

### 4.2. VLMEvalKit & LMMs-Eval (Multimodalidade e RAG Visual)
*   **Suporte Nativo a Múltiplas Modalidades:**
    *   Avaliação unificada de modelos de Visão-Linguagem (VLMs), Áudio-Linguagem e Vídeo-Linguagem.
*   **Benchmarks Multimodais SOTA:**
    *   **MMMU-Pro & MathVista:** Raciocínio universitário e matemático sobre diagramas e figuras.
    *   **DocVQA, ChartQA, InfoVQA & OCRBench:** Leitura de documentos densos, tabelas financeiras e gráficos sem OCR externo.
    *   **POPE & HallusionBench:** Medição rigorosa de alucinação de objetos e ilusões visuais.
    *   **ViDoRe (Visual Document Retrieval):** Benchmark específico para avaliar modelos de RAG Visual baseados em *Late Interaction* de patches de imagem (como **ColPali** e **ColQwen2**).

---

## 5. Monitoramento de Drift e Qualidade de Dados Tabulares e Textuais

### 5.1. Evidently AI
*   **Detecção de Drift Estatístico (Data & Prediction Drift):**
    *   Testes estatísticos automáticos adaptados ao tipo e volume de dados: Teste de Kolmogorov-Smirnov (KS), **PSI (Population Stability Index)**, Distância de Wasserstein (Earth Mover's Distance), Divergência de Kullback-Leibler ($D_{\text{KL}}$) e teste Qui-Quadrado ($\chi^2$).
*   **Descritores de Texto e LLM:**
    *   Monitoramento contínuo de comprimento de sentenças, contagem de tokens, palavras fora do vocabulário (OOV), sentimento, legibilidade, presença de expressões regulares proibidas e declínio semântico de embeddings.
*   **Qualidade de Dados e Integridade:**
    *   Proporção de valores nulos (`NaN`), violação de esquemas de tipos, correlação entre features (Pearson, Spearman, Cramer's V) e detecção de *outliers*.

---

## 6. Matriz Comparativa Consolidada de Funcionalidades

| Capacidade / Feature | W&B / MLflow | DVC / LakeFS | Arize Phoenix | Langfuse / LangSmith | Ragas / DeepEval | Promptfoo | Inspect AI | LM-Eval / VLMEvalKit | Evidently AI |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Tracking de Treino (Loss, GPU, Pesos)** | **Nativo** | Parcial | — | — | — | — | — | — | — |
| **Versionamento de Dados Pesados (Git-like)** | Artefatos | **Nativo** | — | — | — | — | — | — | — |
| **Model Registry (Pesos & Flavors)** | **Nativo** | — | — | — | — | — | — | — | — |
| **Tracing OpenTelemetry (RAG & Agentes)** | Sim | — | **Nativo** | **Nativo** | — | — | **Nativo** | — | — |
| **CMS e Versionamento de Prompts** | Básico | — | **Nativo** | **Nativo** | — | Config | — | — | — |
| **Projeção UMAP 3D de Embeddings** | Básico | — | **Nativo** | — | — | — | — | — | — |
| **Métricas da Tríade RAG & Proposições** | Via Plugin | — | **Nativo** | Via SDK | **Nativo** | Básico | — | — | Básico |
| **Avaliação de Agentes em Sandbox Docker** | — | — | — | — | — | — | **Nativo** | — | — |
| **Red-Teaming & Pentesting (OWASP LLM)** | — | — | — | — | **Nativo** | **Nativo** | Sim | — | — |
| **Benchmarks Fundacionais (Logits / VLM)** | — | — | — | — | — | — | Sim | **Nativo** | — |
| **Data Drift Tabular (PSI, KS, Wasserstein)**| — | — | Vetorial | — | — | — | — | — | **Nativo** |
| **Filas de Anotação Humana (RLHF/DPO)** | Básico | — | Sim | **Nativo** | — | — | — | — | — |

---

## 7. Blueprint de Arquitetura: Sistema Unificado de Observabilidade, Teste e Versionamento

Para construir uma **plataforma única e dinâmica** que integre todas as capacidades acima sem criar silos de dados, a arquitetura deve ser desenhada em **5 Camadas Desacopladas**, unificadas por protocolos abertos.

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                         CAMADA 5: INTERFACE GRÁFICA UNIFICADA (FRONTEND)                        │
│  [1. Training & Registry] [2. Trace & Agent Graph] [3. Prompt & Matrix Lab] [4. Vector & Drift] │
└───────────────────────────────────────────────▲─────────────────────────────────────────────────┘
                                                │ GraphQL / REST / WebSockets (Streaming)
┌───────────────────────────────────────────────┴─────────────────────────────────────────────────┐
│                       CAMADA 4: MOTORES DE AVALIAÇÃO, CI/CD & SAND-BOXING                       │
│  • Ragas/DeepEval Worker (RAG & G-Eval)       • Inspect AI Docker Runner (Sandboxes de Agentes) │
│  • LM-Eval & VLMEvalKit GPU Runner            • Evidently Drift Engine & Red-Team Scanner       │
└───────────────────────────────────────────────▲─────────────────────────────────────────────────┘
                                                │ Filas Assíncronas (Kafka / Redis Streams / Ray)
┌───────────────────────────────────────────────┴─────────────────────────────────────────────────┐
│                          CAMADA 3: CAMADA DE ARMAZENAMENTO POLIGLOTA                            │
│  • ClickHouse (Traces, Spans, Séries Temporais de Loss/GPU, Custos e Logs de Alta Vazão)        │
│  • PostgreSQL + pgvector (Metadados, Model/Prompt Registry, Filas RLHF, Golden Datasets)        │
│  • MinIO / S3 + LakeFS (Pesos .safetensors, Imagens Multimodais, Áudio, Parquet Datasets)       │
└───────────────────────────────────────────────▲─────────────────────────────────────────────────┘
                                                │
┌───────────────────────────────────────────────┴─────────────────────────────────────────────────┐
│                      CAMADA 2: GATEWAY DE INGESTÃO UNIVERSAL & PROXY DE IA                      │
│  • Coletor OpenTelemetry (OTLP gRPC/HTTP) com semântica OpenInference                           │
│  • AI Gateway (Roteamento de LLMs, Fallback, Cache Semântico, Rate Limit e Contagem de Custos)  │
└───────────────────────────────────────────────▲─────────────────────────────────────────────────┘
                                                │ SDK Unificado Leve (Python / TS)
┌───────────────────────────────────────────────┴─────────────────────────────────────────────────┐
│                         CAMADA 1: APLICAÇÕES E TREINAMENTOS INSTRUMENTADOS                      │
│  [Treino PyTorch/TRL/XGBoost]   [Pipelines RAG & ColPali]   [Sistemas Multiagentes/Multimodais] │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 7.1. Módulos Essenciais da Interface Unificada (O que construir na UI)

Para proporcionar acesso rápido e observabilidade total, a interface deve ser organizada em **6 Módulos Conectados**:

1.  **Módulo 1: Model, Data & Prompt Hub (Versionamento Unificado)**
    *   **Model & LoRA Registry:** Catálogo versionado de modelos clássicos (`.pkl`, `.onnx`) e pesos de LLMs/Adaptadores LoRA (`.safetensors`), linkados diretamente ao hash do dataset no LakeFS/DVC e à curva de treinamento.
    *   **Prompt CMS:** Editor versionado de prompts com suporte a variáveis, schemas de *Structured Outputs* (JSON Schema) e botões de promoção (`Dev` $\rightarrow$ `Staging` $\rightarrow$ `Prod`) com atualização instantânea via cache.
2.  **Módulo 2: Training & Fine-Tuning Studio (MLOps & SFT/DPO)**
    *   Painel de séries temporais de alta frequência (Loss, Perplexidade, Gradientes, Margem de Recompensa DPO, telemetria de GPU).
    *   Visualizador de *Sweeps* (Coordenadas Paralelas) e gráficos de explicabilidade de ML clássico (SHAP, ROC-AUC, Matriz de Confusão).
3.  **Módulo 3: Live Trace & Multi-Agent Inspector (AgentOps)**
    *   **Waterfall + Grafo Topológico:** Visualização simultânea da linha do tempo (latência e TTFT de cada span) e do grafo direcionado de agentes (mostrando transições entre Supervisor, Subagentes e chamadas de ferramentas).
    *   **Visualizador Multimodal Nativo:** Renderização inline de imagens enviadas ao VLM, páginas de PDF recuperadas pelo *ColPali*, *bounding boxes* de cliques de agentes de GUI e players de áudio dentro do span.
4.  **Módulo 4: Evaluation Matrix, RAG Triad & Red-Teaming Lab**
    *   **Grade Matricial (Estilo Promptfoo):** Comparação lado a lado de $M$ versões de prompts/modelos contra um *Golden Dataset* com destaque visual de regressões.
    *   **Painel da Tríade RAG:** Gráficos de radar e decomposição sentença por sentença mostrando quais afirmações da resposta foram validadas pelo contexto (*Faithfulness*) e quais alucinaram (em vermelho).
    *   **Scanner de Segurança:** Botão para disparar baterias de ataques OWASP (Prompt Injection, Jailbreak, PII Leak) contra o endpoint do agente.
5.  **Módulo 5: Vector Space & Drift Observatory**
    *   **Projetor UMAP 3D:** Visualização interativa dos embeddings das perguntas dos usuários versus os documentos da base de conhecimento, destacando clusters de tópicos sem cobertura documental (*Knowledge Gaps*).
    *   **Monitor de Drift:** Alertas automáticos de deriva estatística (PSI, Wasserstein, Divergência JS) tanto para features tabulares quanto para embeddings de texto/imagem.
6.  **Módulo 6: Human-in-the-Loop & Dataset Flywheel (O Elo Fechado)**
    *   **Fila de Anotação e Curadoria:** Qualquer trace em produção com nota baixa (ex.: $\text{Faithfulness} < 0.7$ ou *thumbs down* do usuário) cai automaticamente nesta fila.
    *   **Conversão em 1 Clique:** O avaliador humano edita a resposta ideal ou a trajetória correta de ferramentas e salva o registro simultaneamente como:
        1. Caso de teste de regressão para o CI/CD (DeepEval/Ragas).
        2. Par de preferência $(x, y_{\text{win}}, y_{\text{lose}})$ para o próximo ciclo de *Fine-Tuning* via DPO.

### 7.2. Stack Tecnológica Recomendada para Implementação da Plataforma

*   **Protocolo de Ingestão:** **OpenTelemetry (OTLP)** padronizado com as convenções semânticas do **OpenInference** (permite receber dados nativos de LangChain, LlamaIndex, CrewAI, AutoGen, DSPy, LiteLLM e PyTorch sem reescrever integrações).
*   **Banco Analítico de Traces e Métricas:** **ClickHouse** (suporta compressão colunar massiva, buscas em JSON de alta velocidade e agregações de milhões de spans e séries temporais de treino em milissegundos).
*   **Banco Transacional e Vetorial:** **PostgreSQL + pgvector** (gerencia usuários, versionamento de prompts, metadados de modelos, filas de anotação e busca de similaridade rápida).
*   **Armazenamento de Objetos e Dados:** **MinIO / S3** acoplado ao **LakeFS** (para armazenar pesos de modelos, datasets Parquet e mídias multimodais dos traces com versionamento Git-like).
*   **Orquestrador de Jobs de Avaliação:** **Ray** ou **Celery + Redis** (para distribuir o cálculo assíncrono de métricas do Ragas, DeepEval, Evidently e containers Docker do Inspect AI sem bloquear a API principal).
*   **Frontend:** **React / Next.js** com **Apache ECharts / Visx** (para gráficos de séries temporais e coordenadas paralelas), **React Flow** (para grafos de agentes e linhagem DAG) e **Three.js / Deck.gl** (para renderização acelerada por WebGL da projeção UMAP 3D de embeddings).