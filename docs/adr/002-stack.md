# ADR-002: FastAPI + Next.js, monorepo

Status: aceito · 2026-11-09

## Contexto
As bibliotecas de avaliação (Ragas, DeepEval, Evidently, Inspect, lm-eval) são Python. A UI precisa de grafos (React Flow), waterfall e, depois, WebGL (deck.gl).

## Decisão
Backend e workers em Python 3.12+ (FastAPI, pydantic v2, SQLAlchemy 2) no pacote `server/` (`matrix_server`), gerenciado com `uv`. Frontend em Next.js (App Router, TypeScript) em `apps/web`. SDK Python em `sdk/python`. Um monorepo, um `Makefile`, um CI.

## Consequências
- Workers importam as mesmas classes de domínio da API.
- A web fala com a API só no servidor (Route Handlers e Server Components), então a chave do projeto não chega ao navegador.
