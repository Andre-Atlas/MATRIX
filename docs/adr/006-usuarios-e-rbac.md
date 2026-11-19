# ADR-006: usuários, sessões opacas e RBAC por projeto

Status: aceito · 2026-11-10

## Contexto
O ciclo 1 deixou a UI sem login (risco aceito). As chaves de API identificam só o projeto e têm poder total. O ciclo 2 adiciona ações sensíveis (promover modelo, rodar scanner, criar chaves de gateway) que exigem identidade e papel.

## Decisão
- **Usuários** com e-mail único (minúsculo) e senha com argon2id. Login responde igual para usuário inexistente e senha errada, com custo de hash equivalente (hash fictício). Limitação de tentativas por (e-mail, IP) com bloqueio progressivo.
- **Sessões opacas no servidor**: token aleatório de 32 bytes (`mxs_…`), guardado só como SHA-256; expira por inatividade (7 dias) e por idade (30 dias); logout e troca de senha revogam. Sem JWT.
- **Quem guarda o cookie é o Next.js**, não o navegador contra a API: o servidor Next define o cookie `matrix_session` (HttpOnly, SameSite=Lax, Secure fora de dev) e repassa o token à API como `Authorization: Bearer mxs_…` + `X-Matrix-Project`. O navegador nunca fala com a API diretamente, então a API não usa cookie e não precisa de CSRF próprio. O proxy do Next exige `Origin` igual ao host nas mutações.
- **Papéis por projeto**: `viewer < member < admin < owner`. `viewer` lê; `member` escreve dados de trabalho (datasets, prompts, experimentos, anotações, runs); `admin` gerencia membros, chaves, regras, avaliações online, hosts de saída, registry e promoções; `owner` apaga o projeto e gerencia admins.
- **Chaves de API ganham escopo** (`ingest`, `read`, `write`), independentes de papel: `ingest` só `POST /v1/traces`; `read` equivale a viewer; `write` equivale a member. Chaves existentes recebem os três escopos na migração. Chaves nunca têm papel admin.
- **Plataforma**: `MATRIX_ADMIN_TOKEN` continua existindo só para bootstrap (criar o primeiro usuário e projeto) e para a CLI.
- Toda rota declara o papel/escopo mínimo por uma dependência única (`require(...)`), sem checagem espalhada.

## Consequências
- Todos os routers do ciclo 1 migram da dependência `current_project_id` para `require(...)`.
- Auditoria de ações sensíveis em `audit_log` (quem, o quê, quando, projeto).
- Sem SSO/OIDC neste ciclo; a interface de usuário foi desenhada para trocar o provedor depois.
