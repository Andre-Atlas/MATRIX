---
name: atlas-lane-architecture
description: Lane de Arquitetura do ATLAS. Define como decisões estruturais são exploradas, registradas em ADR, transformadas em contratos verificáveis e revisadas. Use quando o maestro ativar o paradigma arquitetura, quando houver nova dependência externa, API pública, mudança de schema, ou uma tarefa com a tag [arch].
---

# Lane Arquitetura

## Quando usar

- O maestro declarou o paradigma `arquitetura`.
- O pedido cria ou muda: módulo, fronteira entre serviços, API pública, schema de banco, dependência externa.
- Uma tarefa do plano tem a tag `[arch]`.

## Como funciona

### Descoberta (fase 2)

1. `code-explorer` mapeia os caminhos de execução afetados e as fronteiras atuais.
2. `architect` (sistema, escala, riscos) ou `code-architect` (blueprint da feature a partir dos padrões do código) propõe **2 ou 3 alternativas** com custos.
3. Escolha de padrão quando aplicável: `hexagonal-architecture` (fronteiras de domínio), `api-design` (REST), `contract-first` (frontend/backend ou serviço a serviço), `backend-patterns`, `database-migrations`.
4. Decisão difícil com trade-off real vai para `council`.
5. A alternativa escolhida vira um **ADR** (`architecture-decision-records`, em `docs/adr/`) que entra na spec do Gate 1.

### Plano (fase 3)

- Contrato antes de implementação: a primeira tarefa `[arch]` escreve o contrato (OpenAPI, JSON Schema, tipos públicos, migração) e o teste que o valida.
- Trabalho que cruza várias sessões ou PRs usa `blueprint` para gerar passos do tamanho de um PR com dependências explícitas.

### Implementação (fase 4, tarefas `[arch]`)

- O implementador carrega este skill e o padrão escolhido.
- Dependências apontam para dentro (domínio não importa infraestrutura).
- Mudança de contrato publicado exige versão nova ou compatibilidade retroativa explícita.

### Revisão (fase 5)

Em paralelo:
- `architect`: aderência ao ADR, acoplamento, pontos de falha.
- `type-design-analyzer`: invariantes e encapsulamento dos tipos novos.
- `database-reviewer`: quando há schema, índice ou consulta nova.
- `security-reviewer`: sempre que uma fronteira nova recebe entrada externa.

## Critério de pronto

- [ ] ADR escrito com contexto, alternativas e consequências
- [ ] contrato verificável com teste automatizado
- [ ] migrações reversíveis (ou motivo registrado no ADR)
- [ ] nenhum achado CRITICAL ou HIGH aberto dos revisores da lane

## Exemplos

Linha do maestro:

```
ATLAS · feature · arquitetura+dados · large → ... · lanes: arquitetura
```

Primeira tarefa do plano:

```
- [arch] Escrever openapi/pedidos.yaml (POST /pedidos, GET /pedidos/{id}) e teste de contrato que falha
```
