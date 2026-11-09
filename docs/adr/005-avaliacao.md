# ADR-005: motor de avaliação próprio com fórmulas do Ragas/DeepEval

Status: aceito · 2026-11-09

## Decisão
Métricas implementadas em `matrix_server.evals` sobre duas interfaces: `LLMProvider` (completar texto) e `Embedder`. Faithfulness = proposições suportadas / proposições extraídas; answer relevancy = cosseno médio entre a pergunta original e N perguntas geradas a partir da resposta; context precision = precisão média ponderada pela relevância de cada chunk; G-Eval = rubrica com CoT e nota 1–5 normalizada. Asserções determinísticas no estilo Promptfoo (equals, contains, regex, is-json com JSON Schema, levenshtein, rouge-l, custo, latência). O provider `fake` é determinístico e roda os testes sem chave.

## Consequências
- Testes e demo rodam offline.
- Adaptadores para `ragas`/`deepeval` podem ser registrados como métricas extras sem mudar o runner.
