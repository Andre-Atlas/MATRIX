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

Este bloco é gerado pelo instalador do ATLAS. Edite fora dos marcadores.
<!-- ATLAS:END -->

## Perguntas Interativas (Checkbox & Multicheck)

Sempre que precisar consultar o usuário para decisões de escopo, arquitetura, design ou priorização entre opções, **use obrigatoriamente a ferramenta interativa de perguntas com opções/checkbox (`ask_question`)** (equivalente ao estilo *checkbox with options / multicheck*), usando `is_multi_select: true` quando múltiplas seleções forem válidas. Evite listas de perguntas em texto simples.

