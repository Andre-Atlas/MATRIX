# ADR-010: motores reais (Inspect AI, lm-eval, VLMEvalKit) num serviço runner isolado

Status: aceito · 2026-11-10

## Contexto
Avaliação de agentes executa código não confiável (comandos `bash`, `python`) e benchmarks fundacionais carregam bibliotecas pesadas (PyTorch, transformers). Nenhum dos dois pode rodar na API nem no worker comum.

## Decisão
- **Usar as implementações reais**: Inspect AI (UK AISI) para tarefas de agente e `lm-evaluation-harness` / `VLMEvalKit` para benchmarks, porque só a implementação padrão produz números comparáveis aos publicados. O MATRIX **orquestra e normaliza**: não reimplementa.
- Um serviço **`matrix-runner`** separado: processo próprio, imagem própria (extras `inspect`, `lmeval`, `vlmeval`), fila própria (`kind` com prefixo `runner.`), sem acesso ao banco de metadados — ele fala com a API do MATRIX por HTTPS usando uma **chave de runner** com escopo mínimo (`runner`, por projeto) para buscar o job, enviar resultados/trace e devolver artefatos. Só o runner enxerga o socket do Docker.
- **Sandbox por amostra**: um container descartável por execução, `--network none` por padrão, usuário sem privilégio (`--user 65534`), `--read-only` com tmpfs de trabalho, `--cap-drop ALL`, `--security-opt no-new-privileges`, limites de CPU/memória/PIDs/tempo, volume efêmero para a amostra, imagem de uma allowlist configurada pelo operador (nunca informada pelo usuário). O runner usa o daemon Docker (ou Podman) do host; documentar que o socket do Docker equivale a root e que o runner deve ficar numa máquina dedicada em produção.
- **Traduz execuções em traces do MATRIX**: cada amostra do Inspect vira um trace com spans AGENT/LLM/TOOL por evento do log (`inspect_ai.log`), preservando mensagens, chamadas de ferramenta, saída do sandbox e score.
- **Benchmarks**: o runner chama `lm_eval` (modo `local-completions`/`openai-chat-completions` contra um endpoint compatível com OpenAI — vLLM, SGLang ou o gateway do MATRIX — ou `hf` para pesos locais em GPU) e `VLMEvalKit`, lê o JSON de resultados e normaliza `(modelo, tarefa, métrica, valor, stderr, n_amostras, few-shot, versão da tarefa)`.

## Consequências
- Dependências pesadas ficam fora da API e do worker.
- Sem GPU neste ambiente, benchmarks reais rodam em CPU com modelo mínimo (ex.: `sshleifer/tiny-gpt2`) e tarefa pequena; números reais de GPU dependem do hardware do usuário (documentado).
- A chave de runner expira e é revogável; todo job do runner é auditado.
