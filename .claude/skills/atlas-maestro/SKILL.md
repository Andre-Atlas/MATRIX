---
name: atlas-maestro
description: Conduz todo pedido de desenvolvimento num projeto com ATLAS instalado. Classifica o pedido (operação, paradigmas, tamanho), escolhe as fases e as lanes, monta o time de agentes e aplica os dois gates humanos. Use no início de qualquer pedido para construir, mudar, corrigir, refatorar, auditar ou desenhar algo.
---

# ATLAS Maestro

O maestro é o regente do fluxo. Ele **não implementa**: decide o caminho, chama as skills e os agentes certos em cada fase e guarda os gates. A sessão principal executa este skill; o agente `maestro` existe só para a classificação inicial quando o pedido é grande ou ambíguo (no Claude Code subagentes não chamam outros subagentes, e em qualquer harness só a sessão principal fala com o usuário, então a regência fica sempre nela).

**Manifesto.** Leia `.atlas/atlas.json` do projeto; se não existir, o ATLAS está instalado globalmente e o manifesto é `~/.atlas/atlas.json`. Toda menção a `.atlas/atlas.json` abaixo vale para o que foi encontrado. Os artefatos de trabalho ficam sempre no projeto, em `docs/atlas/`.

**Harness.** `harnesses` no manifesto diz onde o projeto roda. No Antigravity, leia antes `references/antigravity.md`, que traduz as ferramentas citadas pelos skills (`Task`, `TodoWrite`, `Read`…) para as do Antigravity.

## Quando usar

- Qualquer pedido que vá produzir ou alterar código, design, arquitetura ou um relatório de auditoria.
- Pular só em perguntas factuais que não mudam nada no repositório.

## Passo 0: classificar e declarar

Declare em **uma linha**, antes de qualquer outra coisa, para o usuário poder corrigir:

```
ATLAS · <operação> · <paradigmas> · <tamanho> → <fases> · lanes: <lanes>
```

**Operação** (uma):

| Operação | Quando | Primeiro movimento |
|---|---|---|
| `feature` | a capacidade não existe | pesquisar reuso, depois descoberta |
| `change` | funciona, mas o comportamento desejado é outro | ajustar comportamento **e** seus testes |
| `fix` | está quebrado | `systematic-debugging`, reproduzir com teste que falha |
| `refactor` | comportamento fica, estrutura melhora | manter testes verdes o tempo todo |
| `mvp` | começar a partir de um documento de spec/design | extrair fatias verticais do documento |
| `audit` | diagnosticar sem escrever código | lane Auditoria, sai um relatório |
| `design` | explorar a direção visual antes do código | lane Design, sai um brief de design |

**Paradigmas** (um ou mais): `design`, `motion`, `arquitetura`, `produto`, `auditoria`, `dados`, `infra`. Cada paradigma com lane instalada ativa essa lane (ver `.atlas/atlas.json` → `lanes`). Paradigma sem lane instalada usa só os especialistas do núcleo.

**Tamanho** (o maior que qualquer sinal atingir):

| Tamanho | Arquivos | Dependência ou contrato novo | Ambiguidade |
|---|---|---|---|
| `trivial` | 1, poucas linhas | nenhuma | nenhuma |
| `small` | 1 arquivo ou função | nenhuma | some ao ler o código |
| `standard` | 2 a 5 | talvez um módulo interno | uma escolha real |
| `large` | muitos ou transversal | dependência externa, API pública, spec | várias perguntas abertas |

Tocar em gatilho de segurança (abaixo) ou em contrato/API pública força **no mínimo `standard`**.

Para pedidos `large` ou quando a classificação não for óbvia, delegue ao agente `maestro`, que devolve a mesma linha com justificativa. Despache-o com o papel `classifier` do `atlas-dispatch` (opus/medium).

## Fases por tamanho

| Fase | trivial | small | standard | large |
|---|---|---|---|---|
| 1. Pesquisa e reuso (`search-first`, `code-explorer`) | — | leve | sim | sim |
| 2. Descoberta (`brainstorming` + consultores das lanes) | — | — | sim | sim + `dev-team` ou `council` |
| 3. Plano (`writing-plans`, tarefas com tag de lane e nível `C1`…`C5`) | — | inline | sim | sim + ADR; `blueprint` se cruzar sessões |
| **Gate 1** (spec + plano) | — | — | sim | sim |
| 4. Execução | inline | `executing-plans` | `subagent-driven-development`, despacho via `atlas-dispatch` | SDD em worktrees (`using-git-worktrees`), despacho via `atlas-dispatch` |
| 5. Revisão | `code-reviewer` | + revisor da linguagem | + revisores das lanes + segurança se gatilho | idem + verificação adversarial |
| 6. Verificação (`verification-before-completion`) | testes | sim | sim + checklist da lane | sim + `production-audit` se instalado |
| **Gate 2** (merge/PR) | sim | sim | sim | sim |
| 7. Aprendizado | — | — | registrar decisões | ADR + candidato a skill |

Operação `audit` não tem fases 3, 4 nem Gate 1: vai de 1 direto para a lane Auditoria e termina no relatório.

## Os dois gates

1. **Gate 1, depois do plano.** Mostre a spec, o plano (tarefas com tag de lane e nível de complexidade) e a tabela de custo do `atlas-dispatch` (tarefa · tag · nível · modelo · esforço). Não escreva código de implementação até o usuário aprovar.
2. **Gate 2, antes de integrar.** Mostre o resumo do diff, a evidência de verificação e as mensagens de commit. Não faça merge, push em branch compartilhada nem publicação até o usuário confirmar.

**Entre os gates não se para para perguntar.** Ambiguidade, conflito entre plano e realidade ou defeito do plano viram uma decisão registrada no ledger do plano:

```
Ruling: <o que decidi> — <por quê> — <quanto custa se estiver errado>
```

Só quatro coisas param a execução entre os gates: ação irreversível ou destrutiva; ação sensível de segurança; efeito fora da worktree (push, merge, deploy, publicação); plano tão quebrado que qualquer caminho seria chute.

## Tags de lane no plano

Cada tarefa do plano recebe uma tag. A tag escolhe quem implementa e quem revisa aquela tarefa no SDD:

| Tag | Implementa | Revisa |
|---|---|---|
| `[core]` | implementador padrão do SDD + `tdd-guide` | `code-reviewer` + revisor da linguagem |
| `[design]` | implementador com `atlas-lane-design` carregado | `design-director` + `a11y-architect` |
| `[arch]` | implementador com `atlas-lane-architecture` carregado | `architect` + `type-design-analyzer` |
| `[audit]` | — (só diagnóstico) | `audit-lead` |
| `[data]` | implementador padrão | `database-reviewer` |

Se a lane da tag não estiver instalada, trate como `[core]` e anote no ledger.

## Modelo e esforço por tarefa

A tag escolhe **quem** implementa e revisa; o nível de complexidade escolhe **com qual modelo e esforço**. Cada tarefa do plano leva os dois: `- [ ] T3 [arch] · C5 — schema de spans`. A pontuação, a tabela nível → modelo/esforço, os pisos por papel e o escalonamento estão no skill `atlas-dispatch`. Regras que valem sempre:

- todo subagente despachado recebe `model` e `effort` explícitos, de haiku a opus;
- o esforço nunca passa de `high`, e `high` só aparece na complexidade extrema (`C5`);
- duas falhas na mesma tarefa sobem um nível, com `Ruling:` no ledger.

## Gatilho de segurança

Inclua `security-reviewer` na revisão sempre que o diff tocar: autenticação ou autorização, entrada do usuário, consultas a banco, caminhos de arquivo, chamadas a APIs externas, criptografia, segredos ou credenciais.

## Precedência entre as fontes

ATLAS combina skills do superpowers e do ECC. Quando dois cobrem o mesmo assunto, vale esta ordem:

| Assunto | Dono | Papel do outro |
|---|---|---|
| Descoberta e spec | `brainstorming` (superpowers) | `intent-driven-development`, `product-capability` (ECC) como consultores |
| Formato do plano | `writing-plans` (superpowers) | `blueprint` (ECC) só para trabalho multi-sessão |
| TDD | `test-driven-development` (superpowers) | agente `tdd-guide` (ECC) como executor |
| Execução | `subagent-driven-development` (superpowers) | — |
| Revisão | `requesting-code-review` / `receiving-code-review` (superpowers) | agentes revisores do ECC fazem a revisão em si |
| Verificação | `verification-before-completion` (superpowers) | `verification-loop` (ECC) como checklist executável |
| Gates | **este skill** | o ATLAS substitui os gates do `orch-pipeline` e a execução sem gates do superpowers |
| Finalização | `finishing-a-development-branch` (superpowers) | `git-workflow` (ECC) para convenções de commit |

O ATLAS não instala `using-superpowers`: este skill ocupa o papel de ponto de entrada.

## Quando falta um skill

Se nenhum skill instalado cobre o que a tarefa precisa, procure nesta ordem e pare no primeiro que resolver:

1. **Catálogo do ATLAS.** Veja se um módulo cobre a necessidade (`.atlas/atlas.json` lista os instalados; os demais estão no repositório do ATLAS em `catalog/modules/`). Se cobre, proponha reinstalar com `--with <módulo>`.
2. **Ecossistema, com `find-skills`.** Busque com `npx skills find <termo>` e verifique instalações, autor e estrelas antes de recomendar.
3. **Antes de criar, `skill-scout`.** Procure em fontes locais e no GitHub. Se for preciso criar, o skill novo é escrito no repositório do ATLAS, não no projeto.

**Instalar um skill de fora é um gate.** Ele roda com as permissões do agente, então mostre nome, fonte, instalações e o comando, e só instale (`npx skills add <fonte> --skill <nome> --agent <harness> --yes`, com o harness de `.atlas/atlas.json`) depois que o usuário aprovar. A instalação fica registrada no `skills-lock.json` do projeto. Anote no ledger qual skill foi instalado e por quê; um skill de fora que aparecer em dois projetos é candidato a virar módulo do ATLAS.

## Handoff sem estado oculto

Spec, plano, ledger e relatório de revisão são arquivos (por padrão em `docs/atlas/<data>-<slug>/`). Qualquer sessão retoma a partir deles. Nunca dependa de memória da conversa para saber em que fase o trabalho está.

## Verificação deste skill

- a linha de classificação foi declarada antes do trabalho
- as fases que rodaram batem com o tamanho declarado
- Gate 1 e Gate 2 foram respeitados (Gate 1 só a partir de `standard`)
- `security-reviewer` rodou se e somente se um gatilho foi tocado
- toda decisão tomada entre os gates está no ledger
- toda tarefa do plano tem nível `C1`…`C5`, e todo despacho registrou modelo e esforço no ledger
- nenhum despacho usou esforço acima de `high`

## Exemplos

```
ATLAS · feature · design+arquitetura · standard → pesquisa, descoberta, plano, G1, SDD, revisão, verificação, G2 · lanes: design, arquitetura
```

```
ATLAS · fix · core · small → pesquisa leve, executing-plans, revisão, verificação, G2 · lanes: —
```

```
ATLAS · audit · auditoria+segurança · large → pesquisa, lane auditoria, relatório · lanes: auditoria
```
