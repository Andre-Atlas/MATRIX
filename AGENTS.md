<!-- ATLAS:BEGIN -->
## ATLAS: orquestração de agentes

Este projeto usa o ATLAS. Em todo pedido que vá produzir ou alterar código, design,
arquitetura ou um relatório de auditoria, carregue primeiro o skill `atlas-maestro`
e siga o fluxo dele: declarar a linha de classificação, rodar as fases do tamanho
declarado e respeitar os dois gates (spec + plano, e integração).

- Perfil: `web`
- Lanes instaladas: design, arquitetura, auditoria
- Stacks: react, python
- Manifesto completo: `.atlas/atlas.json`
- Artefatos de trabalho (spec, plano, ledger, revisões): `docs/atlas/` do projeto
- Subagentes por tarefa: modelo (haiku a opus) e esforço pela complexidade, via `atlas-dispatch`; esforço máximo `high`

No Antigravity: delegar a um agente é `invoke_subagent` com o nome do agente de
`.agents/agents/`; a lista de tarefas é um artefato do tipo task. A tradução completa
das ferramentas citadas pelos skills está em `references/antigravity.md`, dentro do
skill `atlas-maestro`. Os dois gates continuam valendo: pare e peça aprovação.

Este bloco é gerado pelo instalador do ATLAS. Edite fora dos marcadores.
<!-- ATLAS:END -->

## Retomada (passagem de bastão)

Trabalho em andamento no branch `ccr-9443eae6-fk5867`. Antes de agir, leia
`docs/atlas/2026-11-17-reconciliacao/HANDOFF.md` (estado, decisões travadas, como religar o
ambiente e verificar) e a especificação da tarefa em `docs/atlas/2026-11-17-reconciliacao/tasks/`
(T5 a T13). Não abra PR nem mexa no `main` sem o usuário pedir.

- Modelos no Antigravity: `haiku→flash`, `sonnet/opus→pro`; esforço não tem equivalente.
- Migrações: agentes em paralelo só escrevem fragmentos em `server/migrations/fragments/`.
- Commits convencionais, por caminho (nunca `git add -A` com agentes em paralelo); sem segredos.

## Perguntas Interativas (Checkbox & Multicheck)

Sempre que precisar consultar o usuário para decisões de escopo, arquitetura, design ou priorização entre opções, **use obrigatoriamente a ferramenta interativa de perguntas com opções/checkbox (`ask_question`)**, com `is_multi_select: true` quando múltiplas seleções forem válidas. Evite listas de perguntas em texto simples.
