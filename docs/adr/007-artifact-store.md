# ADR-007: artifact store endereçado por conteúdo, escopado por projeto

Status: aceito · 2026-11-10

## Contexto
Modelos, adaptadores LoRA, manifestos de dataset, mídia de traces e relatórios precisam de armazenamento de objetos com integridade verificável. LakeFS e DVC aparecem no blueprint como sistemas de versionamento de dados.

## Decisão
- Interface `ArtifactStore` com backend **S3-compatível** (boto3; testado contra RustFS, que substitui o MinIO cujas imagens deixaram de ser publicadas) e backend de **arquivo local** (dev e testes).
- **Chave do objeto = `{project_id}/{sha256[0:2]}/{sha256}`**: endereçado por conteúdo (deduplicação) mas **escopado por projeto**, porque deduplicação entre projetos revelaria a existência de conteúdo (oráculo de hash). Nada fornecido pelo usuário entra na chave.
- Metadados em PostgreSQL (`artifacts`: nome, tipo de mídia, tamanho, sha256, dono lógico). Vários artefatos podem apontar para o mesmo blob; o blob só é apagado pelo GC quando nenhum aponta para ele.
- Upload direto (stream pela API, para arquivos pequenos e o SDK) e **URL pré-assinada** (PUT/GET) para grandes. A conclusão verifica tamanho e, até um limite configurável, recalcula o SHA-256; acima dele registra `verified=false`.
- Cotas por projeto (bytes) e limite por objeto. Downloads sempre com `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`; só imagens de uma allowlist servem inline, com `Content-Security-Policy: sandbox`.
- **LakeFS e DVC são adaptadores opcionais** (ler/apontar para repositórios externos), nunca dependência obrigatória: o manifesto de dataset do MATRIX já dá versionamento imutável com pai e diff.

## Consequências
- Um GC periódico (job) remove blobs órfãos.
- Credenciais do S3 do próprio MATRIX vêm de variáveis de ambiente (não são segredos de provedor de LLM; ver ADR-011).
