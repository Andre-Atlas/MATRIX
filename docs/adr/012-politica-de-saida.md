# ADR-012: política única de saída (egress) para URLs fornecidas pelo usuário

Status: aceito · 2026-11-10

## Contexto
Webhooks de promoção e de regra, alvos do scanner de red-team e destinos do runner chamam URLs escolhidas por usuários. Isso é SSRF por definição.

## Decisão
Um módulo único, `matrix_server.egress`, é o **único** caminho para requisições a URLs de usuário. Regras:
1. Só `http`/`https`, sem credenciais embutidas na URL, portas 80/443 por padrão (configurável).
2. A URL é resolvida **uma vez**; **todos** os IPs resolvidos precisam ser públicos. Bloqueados: loopback, privados (RFC 1918, ULA), link-local (inclui metadados em 169.254.169.254), CGNAT (100.64/10), multicast, reservados, não especificados e IPv4 embutido em IPv6 que caia nessas faixas.
3. A conexão usa **o IP validado** (anti DNS rebinding), mantendo `Host` e SNI originais.
4. Redirecionamentos só são seguidos (máx. 3) revalidando cada salto pelas mesmas regras.
5. Limites de tempo, de tamanho de resposta e de tamanho de requisição.
6. **Modo padrão `allowlist`**: o host precisa estar na lista do projeto (`egress_hosts`, gerida por `admin`; curinga só como `*.dominio`). `MATRIX_EGRESS_MODE=public` libera hosts públicos sem lista. `MATRIX_EGRESS_ALLOW_PRIVATE=1` só existe para desenvolvimento e testes.
7. Cada requisição de saída gera registro em `audit_log` (projeto, host, finalidade, status), sem corpo.
Chamadas a provedores de LLM usam URLs configuradas pelo **operador** por variável de ambiente e não passam por esta política (ver ADR-011).

## Consequências
- Webhooks, scanner, gateway (para URLs de usuário) e runners reutilizam o módulo e herdam as proteções.
- Testes cobrem as faixas bloqueadas, rebinding, redirecionamento para IP privado e IPv6.
