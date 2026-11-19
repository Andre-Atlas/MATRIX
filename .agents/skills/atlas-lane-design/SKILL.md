---
name: atlas-lane-design
description: Lane de Design do ATLAS. Define quem consulta, quem implementa e quem revisa trabalho de interface (UI/UX, design system, acessibilidade) e qual é o critério de pronto. Use quando o maestro ativar o paradigma design ou uma tarefa do plano tiver a tag [design].
---

# Lane Design

## Quando usar

- O maestro declarou o paradigma `design` ou a operação `design`.
- Uma tarefa do plano tem a tag `[design]`.
- Pular quando a mudança não altera nada que o usuário final vê.

## Como funciona

### Descoberta (fase 2)

1. Rode `design-system` em modo auditoria se o projeto já tem UI: extrai tokens (cores, tipografia, espaçamento, sombras) e aponta inconsistências. Se não tem, use-o em modo geração.
2. Peça ao agente `design-director` um **brief de design**: direção visual (`frontend-design-direction`), tokens a respeitar, estados de cada tela (vazio, carregando, erro, sucesso), requisitos de acessibilidade.
3. Para produto novo sem identidade, rode `brand-discovery` antes do brief.
4. O brief entra na spec que vai para o Gate 1.

### Implementação (fase 4, tarefas `[design]`)

- O implementador carrega este skill, `make-interfaces-feel-better` e o skill da stack (ex.: `react-patterns`, `vue-patterns`, `swiftui-patterns`).
- Nenhum valor visual solto: cor, espaço, raio e sombra vêm dos tokens. Valor novo entra primeiro no design system.
- Cada componente nasce com todos os estados do brief e com navegação por teclado.

### Revisão (fase 5)

Em paralelo, sobre o diff das tarefas `[design]`:
- `design-director`: aderência ao brief e aos tokens, polimento visual.
- `a11y-architect`: WCAG 2.2 AA (contraste, foco, rótulos, alvos de toque).
- Revisor da stack (`react-reviewer`, `vue-reviewer`, `swift-reviewer`…).
- `click-path-audit` quando a mudança envolve fluxos com vários botões ou estados encadeados.

## Critério de pronto

- [ ] tokens respeitados (zero cor, espaço ou fonte fora do design system)
- [ ] todos os estados do brief implementados e visíveis
- [ ] WCAG 2.2 AA verificado por `a11y-architect`, sem achado CRITICAL ou HIGH aberto
- [ ] captura de tela de cada estado anexada à evidência do Gate 2
- [ ] teste de componente para cada estado

## Exemplos

Tarefa no plano:

```
- [design] Tela de login: estados vazio, erro de credencial, carregando; usar tokens color.surface e space.4
```

Achado típico de revisão:

```
HIGH a11y — botão "Entrar" com contraste 3.1:1 sobre color.surface (mínimo 4.5:1). Evidência: src/ui/Login.tsx:42
```
