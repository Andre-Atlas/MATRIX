# ADR-004: isolamento por projeto via chave de API

Status: aceito · 2026-11-09

## Decisão
Toda escrita e leitura é escopada por `project_id`, derivado da chave de API (`mx_<prefixo>_<segredo>`). Só o hash SHA-256 é persistido; a busca é pelo prefixo e a comparação por `hmac.compare_digest`. Administração por `MATRIX_ADMIN_TOKEN`. Login de pessoas e RBAC na UI ficam para o próximo ciclo.

## Consequências
- Um vazamento de banco não expõe chaves.
- Toda consulta ao ClickHouse inclui `project_id` no filtro; há teste de isolamento.
