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

- T01 · [arch] · C4 → orquestrador inline (ADRs são decisões do plano)
- T02 · [core] · C2 → sonnet/low (implementer) · revisão pelo orquestrador (scaffold sem lógica; `make test` e `make lint` verdes)
- T03 · [arch] · C5 → opus/high (implementer)
- T04 · [core] · C4 → opus/medium (implementer)
- T05 · [core] · C3 → sonnet/medium (implementer)
- T07 · [core] · C3 → sonnet/medium (implementer)
- T03+T04+T05+T07 · revisão conjunta C5 → opus/high (reviewer + security-reviewer, gatilhos: entrada externa, credenciais)
- T06 · [core] · C3 → sonnet/medium (implementer)
- T08 · [design] · C4 → opus/medium (implementer)

## Progresso
- Revisão F1 (C5, opus/high): 2 HIGH confirmados (amplificação de memória por atributos de resource; duplicatas de retry inflando agregados no ClickHouse), 4 MEDIUM, 8 LOW. Isolamento por projeto, SQL parametrizado, limite de gzip e auth confirmados corretos.
- Correção F1 · C5 → opus/high (implementer) — 1ª rodada de correção, sem escalonamento.
- T09 · [arch] · C4 → opus/medium (implementer) — 272 testes (SQLite + PostgreSQL)
- T10 · [core] · C4 → opus/medium (implementer) — 62 testes
- T11 · [core] · C4 → opus/medium (implementer)
- T12 · [design] · C4 → opus/medium (implementer)
- T15 · [core] · C3 → sonnet/medium (implementer)
- Ruling: bancos de teste separados por agente paralelo (`matrix_test_a/_b/_c`) porque os testes recriam as tabelas — evita interferência entre agentes — custo: nenhum.
- T13 · [core] · C3 → sonnet/medium (implementer) — 32 testes
- T17 · [core] · C4 → opus/medium (implementer)
- Ruling: quatro agentes (correção F1, T11, T12, T17) pararam pelo limite de uso da conta às ~10:00 UTC; o container reiniciou. Retomados às 17:27 UTC a partir do estado salvo, com ClickHouse e Postgres reiniciados — sem perda de trabalho; a janela de 8h estourou por esse intervalo — custo: só tempo.
- T11 · [core] · C4 → opus/medium (implementer) — integrado; create_app liga todos os routers e hooks sem empilhar
- T12 · [design] · C4 → opus/medium (implementer) — 52 testes web
- T14+T16 · [design] · C3 → sonnet/medium (implementer, um agente para as duas por terem o mesmo nível e a mesma área)
- T18 · [core] · C3 → sonnet/medium (implementer)
- Revisão backend T09–T17 · C4 → opus/medium (security-reviewer): 4 HIGH (lease sem renovação, ReDoS na métrica regex, hooks de score ausentes no worker, sem limite de corpo nas rotas JSON), 2 MEDIUM. Sem IDOR nem SSRF.
- Correção backend · C4 → opus/medium (implementer) — 1ª rodada
