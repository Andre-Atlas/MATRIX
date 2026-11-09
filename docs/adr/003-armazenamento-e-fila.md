# ADR-003: armazenamento poliglota e fila de jobs no PostgreSQL

Status: aceito · 2026-11-09

## Contexto
Spans chegam em alto volume e são lidos por agregações (latência, custo, tokens). Metadados (prompts, datasets, experimentos, anotações) são transacionais e relacionais.

## Decisão
- **ClickHouse** guarda spans (`MergeTree`, ordenado por `project_id, trace_id, start_time`, TTL configurável). Interface `SpanStore`, com `MemorySpanStore` para testes e modo dev.
- **PostgreSQL** guarda projetos, chaves, prompts, datasets, experimentos, resultados, scores, anotações, regras, avaliações online, alertas e a fila de jobs. SQLAlchemy genérico permite SQLite em testes e modo dev.
- **Fila de jobs** numa tabela `jobs` com `SELECT … FOR UPDATE SKIP LOCKED` (no SQLite, atualização condicional de status). Celery/Redis e Ray ficam para jobs de GPU.
- **MinIO/S3** entra no compose para mídia e artefatos; o MVP guarda mídia como URL/data URI no span.
- pgvector fica disponível na imagem do Postgres para a fase de Drift.

## Consequências
- Uma dependência a menos (Redis) no MVP.
- Throughput da fila limitado ao Postgres, suficiente para avaliações offline e online por amostragem.
