# MATRIX — Plano do ciclo 2: plataforma completa

Spec: [spec.md](spec.md) · Ledger: [ledger.md](ledger.md)

Roteamento (`atlas-dispatch`): C1 haiku/low · C2 sonnet/low · C3 sonnet/medium · C4 opus/medium · C5 opus/high (teto de esforço). Revisor: piso C2; `security-reviewer`: piso C4.

Cada fatia segue o mesmo molde: **contrato primeiro** (ADR + contrato da API na spec, feito pelo orquestrador) → backend e web em paralelo → revisão (com segurança quando houver gatilho) → correção → `scripts/verify_<fatia>.py` → commit e push.

## Tabela de custo (Gate 1)

| Tarefa | Tag | Nível | Implementa | Revisa |
|---|---|---|---|---|
| **G0 Fundação** | | | | |
| T20 ADR-006/007/012 + contrato | `[arch]` | C4 | orquestrador | — |
| T21 Usuários, sessão, RBAC, escopo de chaves | `[core]` | C5 | opus/high | opus/high + segurança |
| T22 Artifact store (S3/MinIO + local, dedup, URL pré-assinada) | `[arch]` | C4 | opus/medium | opus/medium + segurança |
| T23 Política de egress (bloqueio de rede privada, allowlist) | `[core]` | C4 | opus/medium | segurança opus/medium |
| T24 Web: login, troca de projeto, papéis, loading states | `[design]` | C3 | sonnet/medium | sonnet/medium |
| **G1 Tracking de treino** | | | | |
| T25 ADR-008 + contrato | `[arch]` | C4 | orquestrador | — |
| T26 Runs + séries no ClickHouse + downsampling LTTB | `[arch]` | C5 | opus/high | opus/high |
| T27 SDK tracking + telemetria + callbacks HF/PyTorch/sklearn/XGBoost | `[core]` | C4 | opus/medium | opus/medium |
| T28 Avaliação de ML clássico (ROC, PR, confusão, regressão, importância) | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T29 Web: runs, comparação de curvas, hardware, relatórios | `[design]` | C4 | opus/medium | opus/medium |
| **G2 Sweeps** | | | | |
| T30 Controlador: grid, random, TPE, Hyperband/ASHA | `[core]` | C5 | opus/high | opus/high |
| T31 Agente de sweep no SDK | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T32 Web: coordenadas paralelas e importância | `[design]` | C3 | sonnet/medium | sonnet/medium |
| **G3 Registry, dados e linhagem** | | | | |
| T33 Model & LoRA Registry (versões, aliases, estágios, webhook) | `[arch]` | C4 | opus/medium | opus/medium + segurança |
| T34 Versionamento de datasets (manifesto, diff, snapshot dos golden) | `[arch]` | C4 | opus/medium | opus/medium |
| T35 Linhagem (grafo) + adaptadores LakeFS/DVC | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T36 Web: registry, versões de dataset, DAG de linhagem | `[design]` | C3 | sonnet/medium | sonnet/medium |
| **G4 Vector & Drift** | | | | |
| T37 ADR-009 + contrato | `[arch]` | C4 | orquestrador | — |
| T38 Store de embeddings + ingestão + job de embedding | `[arch]` | C4 | opus/medium | opus/medium |
| T39 Projeção UMAP/PCA, HDBSCAN, lacunas de conhecimento | `[core]` | C4 | opus/medium | opus/medium |
| T40 Estatística de drift + qualidade de dados + monitores | `[core]` | C5 | opus/high | opus/high |
| T41 Web: projetor 3D (deck.gl) | `[design]` | C4 | opus/medium | opus/medium |
| T42 Web: painel de drift e monitores | `[design]` | C3 | sonnet/medium | sonnet/medium |
| **G5 Red-teaming** | | | | |
| T43 Biblioteca de ataques + detectores (OWASP LLM) | `[core]` | C4 | opus/medium | opus/medium |
| T44 Alvos, jobs de scan, relatório, achado → dataset | `[core]` | C4 | opus/medium | segurança opus/medium |
| T45 Web: scanner e relatório por categoria | `[design]` | C3 | sonnet/medium | sonnet/medium |
| **G6 Agentes em sandbox** | | | | |
| T46 ADR-010 + serviço runner isolado | `[arch]` | C5 | opus/high | opus/high + segurança |
| T47 Adaptador Inspect AI: tarefas, logs → traces | `[core]` | C4 | opus/medium | opus/medium |
| T48 Web: tarefas, execuções, transcrição | `[design]` | C3 | sonnet/medium | sonnet/medium |
| **G7 Benchmarks** | | | | |
| T49 Adaptadores lm-eval e VLMEvalKit + leaderboard | `[core]` | C4 | opus/medium | opus/medium |
| T50 Web: leaderboard e comparação base × fine-tune | `[design]` | C3 | sonnet/medium | sonnet/medium |
| **G8 AI Gateway** | | | | |
| T51 ADR-011 + contrato | `[arch]` | C4 | orquestrador | — |
| T52 Proxy OpenAI-compatível com streaming, rotas, fallback, retries | `[arch]` | C5 | opus/high | opus/high + segurança |
| T53 Rate limit, orçamento, cache exato e semântico, A/B | `[core]` | C4 | opus/medium | opus/medium |
| T54 Trace automático + custo | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T55 Web: chaves virtuais, rotas, consumo | `[design]` | C3 | sonnet/medium | sonnet/medium |
| **G9 Eval+ e acabamento** | | | | |
| T56 Métricas novas (RAG, agente, conversa, DAG) | `[core]` | C4 | opus/medium | opus/medium |
| T57 Gerador de dataset sintético | `[core]` | C3 | sonnet/medium | sonnet/medium |
| T58 Comparação cega par a par (API + web) | `[design]` | C3 | sonnet/medium | sonnet/medium |
| T59 Inspector multimodal (PDF, áudio, bounding boxes) | `[design]` | C3 | sonnet/medium | sonnet/medium |
| T60 Ação de regra `webhook` assinada | `[core]` | C3 | sonnet/medium | segurança opus/medium |
| **Fecho** | | | | |
| T61 Compose completo executado, `verify_*` de todas as fatias, docs | `[core]` | C3 | sonnet/medium | — |
| T62 Auditoria final (lane Auditoria) | `[audit]` | C4 | auditores sonnet/medium | `audit-lead` opus/medium |

Distribuição: 6 tarefas C5 (opus/high), 17 C4, 16 C3, 4 inline. É cerca de 3 vezes o volume do ciclo 1, que sozinho consumiu um limite de uso de 5 horas da conta.

## Ordem

G0 → G1 → G2 → G3 → G4 → G5 → G8 → G6 → G7 → G9 → fecho.

- G0 vem primeiro porque RBAC, artifact store e egress são usados por todas as outras.
- G1–G3 formam o bloco MLOps (o registry depende de runs e artefatos).
- G8 (gateway) antes de G6/G7 porque runners de agentes e benchmarks podem apontar para o gateway.
- Cada fatia termina com commit e push; se o limite de uso interromper, o que já foi enviado está íntegro e o ledger diz onde retomar.

## Gatilhos de segurança

G0 inteira (auth, credenciais, arquivos), T33 (webhook), T44 e T60 (requisições para URLs do usuário → SSRF), T46 (execução de código em container, acesso ao Docker), T52 (proxy com segredos de provedor). `security-reviewer` roda em todas.

## Estado para retomada

Ver seção "Progresso" do ledger.
