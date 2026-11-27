# ADR-016: tudo local com Ollama e endpoints de modelo confiáveis pelo operador

Status: aceito · 2026-11-27

## Contexto
O ciclo 3 tem uma restrição do usuário que vale acima das outras: **tudo roda localmente, sem gasto** com deploy, nuvem ou API paga. Os motores que hoje são stubs (`EngineUnavailable`) ou simulados (lm-eval, VLMEvalKit, Inspect AI, UMAP/HDBSCAN, alvo do safety) precisam de um modelo de verdade para sair com `simulated=false`, e esse modelo tem de rodar numa máquina comum (Mac arm64 ou Linux, sem GPU).

A política de saída (ADR-012) bloqueia loopback e redes privadas para qualquer URL. Um servidor de modelo local vive justamente em `127.0.0.1` ou na rede interna do compose, então hoje nenhum motor conseguiria chamá-lo pelo módulo `egress` sem `MATRIX_EGRESS_ALLOW_PRIVATE=1`, que abre todas as redes privadas para todas as finalidades e só existe para testes.

## Decisão
1. **Um servidor de modelo local para tudo: Ollama.** Serviço `ollama` no `deploy/compose.yaml`, perfil `llm`, imagem com tag fixa, volume nomeado `ollama-data`, porta só em `127.0.0.1:11434`. Modelos padrão pequenos, para CPU:
   - `qwen2.5:0.5b` para chat e completions (`MATRIX_LOCAL_CHAT_MODEL`);
   - `all-minilm` para embeddings (`MATRIX_LOCAL_EMBED_MODEL`);
   - `moondream` para visão, só no VLMEvalKit (`MATRIX_LOCAL_VISION_MODEL`).

   `make llm-up` sobe o serviço e `make llm-pull` baixa os três modelos.
2. **Endpoints de modelo locais confiáveis pelo operador.** `MATRIX_LOCAL_MODEL_ENDPOINTS` é uma lista de URLs base separadas por vírgula (ex. `http://ollama:11434,http://127.0.0.1:11434`), lida só do ambiente do operador. O padrão é vazio, e vazio não libera nada. O acesso a esses hosts passa só por `egress.local_model_client(purpose=...)`, com estas regras:
   - vale só para as finalidades `model` e `embedding`; qualquer outra é recusada;
   - esquema, host e porta têm de ser exatamente os de uma entrada da lista (a porta padrão do esquema não casa com uma porta explícita diferente); o caminho é livre;
   - cada salto de redirecionamento é verificado contra a mesma lista; destino fora dela é bloqueado;
   - o IP pode ser privado ou loopback, mas link-local, metadados de nuvem, multicast e não especificado continuam proibidos mesmo listados;
   - a lista é lida ao criar o cliente e fica congelada; nenhuma rota, corpo ou parâmetro de requisição a altera ou amplia;
   - mesma fixação de IP, limites, `trust_env=False` (sem proxy herdado) e linha de auditoria `egress.request` do ADR-012, com `local: true` no detalhe. Sem sessão de banco (sondas de saúde), a tentativa vai para o log.

   `egress_client(...)`, usado para URLs de usuário, **nunca** consulta essa lista: um webhook ou alvo de scanner apontado para o Ollama continua bloqueado.
3. **URL dos motores.** `MATRIX_LOCAL_LLM_URL` (padrão `http://127.0.0.1:11434`; no compose `http://ollama:11434`) é a base que os motores usam. `resolve_local_model_base_url()` só a devolve se ela estiver em `MATRIX_LOCAL_MODEL_ENDPOINTS`; se não estiver, devolve `None` e o motor responde `EngineUnavailable`. `local_llm_available(timeout)` faz `GET /api/tags` (Ollama) ou `/v1/models` (OpenAI) e devolve `bool`, para motores e testes decidirem se há modelo.
4. **Compatível com OpenAI.** Os motores falam a API compatível com OpenAI que o Ollama expõe em `/v1`. Por isso o operador pode trocar o Ollama por outro servidor local com a mesma configuração.

## Alternativas consideradas
- **llama.cpp (`llama-server`)**: binário leve e rápido em CPU, API compatível com OpenAI. Exige baixar e gerenciar arquivos GGUF à mão e um processo por modelo (chat, embedding e visão separados). Continua suportado: basta listar a URL dele em `MATRIX_LOCAL_MODEL_ENDPOINTS` e apontar `MATRIX_LOCAL_LLM_URL` para ela.
- **vLLM**: melhor vazão, mas pede GPU para ser útil e não roda em Mac. Fica como opção do operador com GPU, pela mesma configuração.
- **APIs pagas (OpenAI, Anthropic)**: violam a restrição de custo. Os provedores continuam no gateway (ADR-011) para quem quiser, mas nenhum motor depende deles.
- **`MATRIX_EGRESS_ALLOW_PRIVATE=1` em produção**: abriria toda rede privada para toda finalidade, inclusive webhooks de usuário. Rejeitado.
- **Entrada no `egress_hosts` do projeto**: a lista é gerida por admins de projeto pela API, ou seja, vem de dados de requisição. Uma exceção para IP privado não pode depender disso. Rejeitado.

## Consequências
- Os motores reais (L3 a L6) rodam sem custo numa máquina comum, com subconjuntos e modelos de 0,5B. Os números provam que o motor real roda, não servem para comparar modelos.
- A exceção ao ADR-012 é a parte mais sensível do ciclo: fica num módulo só (`egress/local_models.py`), com testes de SSRF (finalidade errada, porta errada, host privado não listado, redirect para fora da lista, metadados listados, lista vazia, lista imutável) e exige revisão de segurança.
- Quem lista um host assume que ele é confiável: tudo o que o servidor de modelo devolver chega aos motores. Listar algo além do servidor de modelo (ex. o Postgres) é erro de configuração do operador.
- O cliente atual lê a resposta inteira (com limite de tamanho); streaming (SSE) para o gateway fica para L2.
- O Ollama ocupa alguns GB de disco no volume `ollama-data` e fica fora do `docker compose up` padrão (perfil `llm`).
