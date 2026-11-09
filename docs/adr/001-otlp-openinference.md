# ADR-001: OTLP com convenções OpenInference como protocolo de ingestão

Status: aceito · 2026-11-09

## Contexto
O MATRIX precisa receber traces de LangChain, LlamaIndex, CrewAI, DSPy, LiteLLM, SDKs da OpenAI/Anthropic e código próprio, sem escrever uma integração por framework.

## Decisão
O único caminho de ingestão é OTLP/HTTP em `POST /v1/traces` (protobuf e JSON), interpretado pelas convenções semânticas do OpenInference (`openinference.span.kind`, `llm.*`, `input.*`, `output.*`, `retrieval.documents.*`, `tool.*`, `session.id`, `user.id`). Spans sem OpenInference são aceitos como `UNKNOWN`/`CHAIN`, com atributos `gen_ai.*` do OpenTelemetry mapeados quando presentes. O SDK do MATRIX só configura o exportador OTLP padrão.

## Consequências
- Qualquer instrumentor OpenInference funciona sem código novo.
- gRPC fica para depois (um coletor OpenTelemetry pode converter gRPC → HTTP na frente do MATRIX).
- O normalizador é a peça mais crítica e tem a maior cobertura de testes.
