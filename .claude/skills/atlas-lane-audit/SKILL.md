---
name: atlas-lane-audit
description: Lane de Auditoria do ATLAS. Monta um time paralelo de auditores (produção, segurança, falhas silenciosas, performance, testes), consolida e verifica os achados e entrega um relatório priorizado que vira backlog de correções. Use na operação audit, antes de lançamentos, ou quando o maestro ativar o paradigma auditoria.
---

# Lane Auditoria

## Quando usar

- Operação `audit` (diagnóstico sem escrever código).
- Pré-lançamento ou pós-merge grande: "o que quebra em produção?".
- Fase 6 de trabalho `large` quando esta lane está instalada.

## Como funciona

### 1. Escopo

Declare o alvo (repositório inteiro, um diretório ou um diff) e as dimensões que rodam. Padrão:

| Dimensão | Quem audita | Roda quando |
|---|---|---|
| Prontidão de produção | skill `production-audit` | sempre |
| Segurança | agente `security-reviewer` + skill `security-scan` | sempre |
| Falhas silenciosas | agente `silent-failure-hunter` | sempre |
| Performance | agente `performance-optimizer` | há UI, API com carga ou laço pesado |
| Qualidade dos testes | agente `pr-test-analyzer` | alvo é um diff ou PR |
| Fluxos de interface | skill `click-path-audit` | há UI com estados encadeados |
| Apps com LLM | skill `agent-architecture-audit` | o projeto chama modelos de linguagem |
| Comentários e docs | agente `comment-analyzer` | auditoria completa |

### 2. Rodar em paralelo

Despache uma auditoria por dimensão ao mesmo tempo (`dispatching-parallel-agents`). Cada auditor devolve achados neste formato:

```
<SEVERIDADE> <dimensão> — <título>
Evidência: <arquivo:linha ou comando e saída>
Impacto: <o que acontece em produção>
Correção sugerida: <uma frase>
```

Severidades: CRITICAL, HIGH, MEDIUM, LOW.

### 3. Consolidar e verificar

Entregue todos os achados ao agente `audit-lead`, que:
1. funde duplicatas pela evidência (o mesmo trecho apontado por dois auditores vira um achado com as duas dimensões);
2. verifica cada CRITICAL e HIGH de forma adversarial, tentando refutar; sem prova reproduzível, o achado cai para MEDIUM com a nota "não confirmado";
3. ordena por severidade e esforço.

### 4. Relatório e backlog

Salve o relatório em `docs/atlas/auditorias/<data>-<alvo>.md`. Cada achado confirmado vira um item com operação `fix` ou `refactor`, pronto para o maestro rodar depois.

## Critério de pronto

- [ ] escopo e dimensões declarados
- [ ] cada CRITICAL e HIGH passou pela verificação adversarial
- [ ] relatório salvo com evidência em todo achado
- [ ] backlog de correções gerado

## Exemplos

```
ATLAS · audit · auditoria+segurança · large → pesquisa, lane auditoria, relatório · lanes: auditoria
```

Item de backlog gerado:

```
fix · small — try/catch vazio engole erro do pagamento em src/billing/charge.ts:88 (HIGH, confirmado)
```
