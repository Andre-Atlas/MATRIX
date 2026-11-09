# Ledger — MATRIX MVP LLMOps

## Decisões do usuário (antes do Gate 1)

- Repositório: `Andre-Atlas/MATRIX`, branch `ccr-9443eae6-fk5867`.
- Primeiro MVP: fatia LLMOps (ingestão → inspector → datasets/eval → anotação).
- Roteamento de subagentes por complexidade (C1–C5), esforço máximo `high`; o maestro pontua no plano.
- Janela autônoma de 8h; revisão só no fim. Aprovação do plano = Gate 1. Gate 2 (PR/merge) fica com o usuário.

## Rulings

- Ruling: ATLAS instalado a partir das cópias locais (`--atlas-path`, `--ecc-path`, `--superpowers-path`) porque o roteamento por complexidade ainda está no branch `ccr-9443eae6-fk5867` do ATLAS — o manifesto marca as fontes como `local`; depois do merge no ATLAS, rodar `npx github:Andre-Atlas/ATLAS update` para fixar commits — custo se errado: manifesto sem refs fixadas até o update.
- Ruling: paradigmas `dados` e `infra` sem lane; uso `clickhouse-io`, `postgres-patterns` e `eval-harness` do ECC só como referência de leitura, sem instalar fora do catálogo — candidato a módulo `stack-data` no ATLAS — custo: nenhum no código.
- Ruling: fila de jobs em PostgreSQL (`FOR UPDATE SKIP LOCKED`) no lugar de Celery + Redis no MVP (ADR-003) — uma peça de infraestrutura a menos; Celery/Ray entram quando houver jobs de GPU — custo se errado: trocar o adaptador de fila.
- Ruling: métricas RAG implementadas no próprio MATRIX seguindo as fórmulas do Ragas/DeepEval (proposições + NLI por juiz, perguntas reversas + cosseno, G-Eval), com provider plugável; `ragas`/`deepeval` ficam como extras opcionais — evita dependências pesadas e chaves obrigatórias nos testes — custo: divergência numérica pequena frente às bibliotecas.
- Ruling: UI sem login no MVP (chave de projeto só no servidor do Next.js) — risco aceito para implantação em rede privada; primeiro item do próximo ciclo — custo: não expor a UI na internet até lá.
- Ruling: o primeiro push cria o branch `ccr-9443eae6-fk5867` num repositório vazio, então o GitHub o torna o branch padrão; não crio `main` (seria push em branch compartilhado) — o usuário cria `main` no Gate 2 — custo: branch padrão temporário.

## Despachos

Formato: `T<n> · [tag] · C<n> → modelo/esforço (papel)`.

## Progresso
