---
name: atlas-dispatch
description: Roteamento de modelo e esforço do ATLAS. Pontua a complexidade de cada tarefa do plano (C1 a C5) e, a cada subagente despachado, escolhe o modelo (haiku, sonnet ou opus) e o grau de esforço (low, medium, high; nunca acima de high) pelo nível da tarefa e pelo papel do agente. Use ao escrever o plano e antes de toda chamada que despacha um subagente.
---

# ATLAS Dispatch

O maestro é o orquestrador: só a sessão principal despacha subagentes (no Claude Code subagente não chama subagente). Este skill diz **com qual modelo e qual esforço** cada despacho roda. A tarefa difícil ganha o modelo forte; a mecânica, o barato. O esforço tem teto: **`high`, e só na complexidade extrema**. `xhigh` e `max` nunca são usados.

A tabela canônica está em `routing.json`, nesta pasta. As tabelas abaixo a espelham (um teste do ATLAS garante que não divergem).

## Quando usar

- **Fase 3 (plano):** toda tarefa recebe um nível `C1`…`C5` ao lado da tag de lane: `[core] · C3`.
- **Gate 1:** mostre a tabela de custo (tarefa · tag · nível · modelo · esforço) junto com a spec e o plano.
- **Fases 4 a 6:** antes de cada despacho de subagente: implementador do SDD, revisores, auditores, verificador adversarial, `maestro` classificador, exploração.
- Execução inline (tamanhos `trivial` e `small`) não despacha subagente e não usa este skill.

## Como funciona

### 1. Pontuar a tarefa

Avalie cada sinal; **o maior nível atingido vence** (mesma regra do tamanho no `atlas-maestro`). Na dúvida entre dois níveis, escolha o maior.

| Sinal | C1 | C2 | C3 | C4 | C5 |
|---|---|---|---|---|---|
| Natureza | mecânica: renomear, formatar, docs, config trivial | lógica local direta | feature com testes, vários casos | desenho de módulo, integração entre partes | núcleo do sistema, sem precedente no código |
| Arquivos | 1 | 1 a 2 | 2 a 5 | muitos | transversal |
| Contrato | nenhum | nenhum | interno | API pública, schema novo, dependência externa | protocolo, schema central de armazenamento, migração de dados |
| Ambiguidade | nenhuma | some ao ler o código | uma escolha real | várias escolhas acopladas | pesquisa necessária |
| Domínio | — | — | — | segurança, concorrência local, performance | concorrência distribuída, algoritmo/matemática não trivial, consistência de dados |
| Reversão | trivial | trivial | fácil | custosa | difícil ou irreversível |

**Gatilho de segurança** (o mesmo do `atlas-maestro`) força no mínimo `C4`.

### 2. Nível → modelo e esforço

| Nível | Nome | Modelo | Esforço |
|---|---|---|---|
| `C1` | mecânica | `haiku` | `low` |
| `C2` | simples | `sonnet` | `low` |
| `C3` | padrão | `sonnet` | `medium` |
| `C4` | complexa | `opus` | `medium` |
| `C5` | extrema | `opus` | `high` |

### 3. Ajustar pelo papel

O nível efetivo de um despacho é o nível da tarefa limitado pelo piso e pelo teto do papel:

| Papel | Quem | Piso | Teto |
|---|---|---|---|
| `implementer` | implementador do SDD, `tdd-guide`, implementador de lane | C1 | — |
| `reviewer` | `code-reviewer`, revisor da linguagem, revisores de lane | C2 | — |
| `security-reviewer` | `security-reviewer` | C4 | — |
| `adversarial-verifier` | verificador adversarial da revisão `large` | C4 | — |
| `audit-lead` | `audit-lead` | C4 | — |
| `classifier` | agente `maestro` | C4 | C4 |
| `explorer` | `Explore`, `code-explorer`, buscas só de leitura | C1 | C2 |

Revisor de tarefa `C5` roda em `C5`; implementador de tarefa `C1` roda em `haiku`; exploração nunca passa de `sonnet/low`.

### 4. Despachar

- **Claude Code:** toda chamada `Task`/`Agent` passa `model` e `effort` explicitamente, mesmo para agentes nomeados com `model` fixo no frontmatter (o parâmetro da chamada vale sobre o do agente). Se o modelo escolhido não aceitar `effort`, omita só o esforço.
- **Nunca** passe `effort: xhigh` nem `effort: max`, nem que o usuário peça "o máximo" para a tarefa: o teto do ATLAS é `high`. Se o usuário quiser mais, isso muda `routing.json` no repositório do ATLAS, não o despacho.
- **Antigravity:** `haiku` vira `flash`; `sonnet` e `opus` viram `pro`. O esforço não tem equivalente e é ignorado; anote isso uma vez no ledger.

### 5. Escalonar

Se a mesma tarefa falhar **2 vezes** (revisor reprova ou verificação falha), suba **1 nível** (até `C5`) e despache de novo. Registre no ledger:

```
Ruling: T7 escalada C3 → C4 (opus/medium) — revisor reprovou 2x por condição de corrida — custo maior só nesta tarefa
```

Nunca rebaixe o nível no meio de uma tarefa. Em `C5` que ainda falha, a tarefa vira "plano quebrado" (uma das quatro paradas do `atlas-maestro`).

### 6. Registrar

O ledger guarda uma linha por despacho de tarefa:

```
T3 · [arch] · C5 → opus/high (implementer) · revisão C5 → opus/high (reviewer) · segurança C5 → opus/high
```

## Exemplos

Tabela de custo apresentada no Gate 1:

| Tarefa | Tag | Nível | Implementa | Revisa |
|---|---|---|---|---|
| T1 Atualizar README e badges | `[core]` | C1 | haiku/low | sonnet/low |
| T2 Endpoint de listagem com paginação | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T3 Schema ClickHouse de spans e ingestão OTLP | `[arch]` | C5 | opus/high | opus/high |
| T4 Login com chave de API (gatilho de segurança) | `[core]` | C4 | opus/medium | opus/medium + `security-reviewer` opus/medium |

Despacho do implementador de T2 no Claude Code: `Agent(subagent_type: "general-purpose", model: "sonnet", effort: "medium", prompt: <spec, plano, ledger e a tarefa T2>)`.

Exploração antes do plano, num pedido `large`: `Agent(subagent_type: "Explore", model: "sonnet", effort: "low", …)`.
