# ADR-011: AI Gateway no mesmo pacote, processo próprio, segredos só por referência

Status: aceito · 2026-11-10

## Decisão
- **`matrix-gateway`** é um processo FastAPI separado (mesmo pacote `matrix_server`, outra porta), para escalar e falhar independente da API. Fala com os provedores de LLM e grava traces/custos por OTLP interno e pela fila.
- **Segredos de provedor nunca vão ao banco.** Uma rota referencia o **nome** de uma variável de ambiente do operador (`api_key_env`), resolvida a cada chamada. Rotas, chaves virtuais, políticas e orçamentos ficam no PostgreSQL.
- **Chaves virtuais** (`mxg_…`, só o hash é guardado) com escopo de projeto, lista de modelos permitidos, limite de requisições e tokens por minuto, orçamento (USD/dia, mês) e validade. Chave virtual não é chave de API do MATRIX e não acessa a API de dados.
- URLs de provedor são **configuradas pelo operador** (variável de ambiente ou configuração do servidor) e não passam pela política de egress dos usuários (ADR-012); o gateway nunca aceita URL de provedor vinda do corpo da requisição de inferência.
- Limitação de taxa e orçamento são aplicados no gateway com contadores em PostgreSQL (janela deslizante simples; Redis como backend opcional); cache exato por hash do corpo normalizado (por chave virtual/projeto, TTL) e cache semântico por embedding com limiar configurável.

## Consequências
- O corpo das requisições (prompts) é dado sensível: o trace guarda conteúdo conforme a política do projeto (`store_content: true|false|hash_only`).
- Streaming exige proxy de SSE sem buffer; TTFT é medido no próprio gateway.
