# ATLAS no Google Antigravity

Os skills do ATLAS, do superpowers e do ECC foram escritos pensando no Claude Code e citam ferramentas dele. No Antigravity (app, IDE ou CLI `agy`) use esta tradução. O fluxo, as fases, os gates e o ledger não mudam.

## Ferramentas

| O skill pede | Claude Code | Antigravity |
|---|---|---|
| Carregar um skill | ferramenta `Skill` | o skill aparece pela `description`; leia `.agents/skills/<nome>/SKILL.md` com `view_file` ou chame `/<nome>` |
| Despachar um agente nomeado (`code-reviewer`, `design-director`…) | `Task` com `subagent_type` | `invoke_subagent` com o agente de `.agents/agents/<nome>.md` |
| Implementador genérico do SDD | subagente `general-purpose` | `invoke_subagent` com o tipo embutido `self` |
| Exploração só de leitura | subagente `Explore` | `invoke_subagent` com o tipo embutido `research` |
| Trabalho isolado em worktree (`using-git-worktrees`, tamanho `large`) | worktree criada pelo skill | `invoke_subagent` com workspace `branch` (worktree própria) |
| Agentes em paralelo (`dispatching-parallel-agents`, lane Auditoria) | várias chamadas `Task` na mesma resposta | várias chamadas `invoke_subagent` sem esperar; acompanhe no painel `/agents` |
| Lista de tarefas (`TodoWrite`) | `TodoWrite` | artefato do tipo task: checklist em markdown que você edita a cada passo |
| Ler, criar, editar arquivo | `Read`, `Write`, `Edit` | `view_file`, `write_to_file`, `replace_file_content` |
| Buscar texto, achar arquivo | `Grep`, `Glob` | `grep_search`, `find_by_name` |
| Rodar comando | `Bash` | `run_command` |
| Web | `WebSearch`, `WebFetch` | `search_web`, `read_url_content` |

## Diferenças que mudam o comportamento

- **Subagente começa sem o histórico da conversa.** Passe no prompt os caminhos da spec, do plano e do ledger em `docs/atlas/<data>-<slug>/`, a tarefa e a tag de lane. É a regra "handoff sem estado oculto" levada a sério.
- **Subagentes podem abrir subagentes** (até 10 níveis). Mesmo assim, a regência fica na conversa principal: só ela declara a linha ATLAS, guarda os gates e escreve no ledger.
- **Modelos.** Os agentes instalados já vêm com `pro` (onde o original pedia opus ou sonnet) e `flash` (onde pedia haiku). No despacho por tarefa (`atlas-dispatch`), nível `C1` usa `flash` e `C2` a `C5` usam `pro`. O grau de esforço não tem equivalente no Antigravity: ignore-o e anote isso uma vez no ledger.
- **Permissões herdadas.** Subagentes herdam a política de comandos da conversa principal. Não troque a política para aprovar tudo automaticamente durante um trabalho ATLAS: o Gate 2 depende de você parar antes de push, merge ou deploy.

## Gates no Antigravity

- **Gate 1.** Salve a spec e o plano em `docs/atlas/<data>-<slug>/` e apresente os dois como artefatos para revisão. Não escreva código até o usuário aprovar (ele pode comentar direto no artefato).
- **Gate 2.** Mostre o resumo do diff, a evidência de verificação e as mensagens de commit, e pare. Push, merge e publicação só depois da confirmação.

## Instalar um skill de fora

Mesmo gate do `SKILL.md`, com o harness do projeto:

```bash
npx skills add <fonte> --skill <nome> --agent antigravity --yes
```
