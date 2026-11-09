#!/usr/bin/env python3
"""Verificação ponta a ponta do MVP do MATRIX (critérios da seção 6 da spec).

Sobe `matrix-api` (porta 8765) e `matrix-worker` com ClickHouse + PostgreSQL reais, roda o demo
de RAG e confere cada critério pela API HTTP. Código de saída 0 só se todos passarem.

Pré-requisitos: ClickHouse em :8123, PostgreSQL com o esquema aplicado (alembic upgrade head) e uv.
Variáveis opcionais: MATRIX_DATABASE_URL, MATRIX_CLICKHOUSE_URL, MATRIX_CLICKHOUSE_DATABASE.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_TOKEN = "verify-admin-token"
RUN = time.strftime("%Y%m%d-%H%M%S")
POLL = 60.0  # seconds


class Check:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.ok = True

    def expect(self, cond: bool, message: str) -> None:
        self.notes.append(("ok   " if cond else "FAIL ") + message)
        self.ok = self.ok and bool(cond)


def poll(fn: Callable[[], Any], timeout: float = POLL, every: float = 0.5) -> Any:
    """Calls fn until it returns something truthy; returns the last value (maybe falsy) on timeout."""
    deadline = time.monotonic() + timeout
    value = fn()
    while not value and time.monotonic() < deadline:
        time.sleep(every)
        value = fn()
    return value


def start(cmd: list[str], env: dict[str, str], log: Path) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, cwd=ROOT, env=env, stdout=log.open("wb"), stderr=subprocess.STDOUT, start_new_session=True
    )


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="matrix-verify-"))
    env = {
        **os.environ,
        "MATRIX_SPAN_STORE": "clickhouse",
        "MATRIX_CLICKHOUSE_URL": os.environ.get("MATRIX_CLICKHOUSE_URL", "http://localhost:8123"),
        "MATRIX_CLICKHOUSE_DATABASE": os.environ.get("MATRIX_CLICKHOUSE_DATABASE", "matrix_demo"),
        "MATRIX_DATABASE_URL": os.environ.get(
            "MATRIX_DATABASE_URL", "postgresql+psycopg://matrix:matrix@localhost:5432/matrix"
        ),
        "MATRIX_ADMIN_TOKEN": ADMIN_TOKEN,
        "MATRIX_JUDGE_PROVIDER": "fake",
        "MATRIX_HOST": "127.0.0.1",
        "MATRIX_PORT": str(PORT),
    }
    procs: list[subprocess.Popen] = []
    results: list[tuple[str, bool, list[str]]] = []
    try:
        print(f"Logs do servidor em {tmp}")
        procs.append(start(["uv", "run", "--project", "server", "matrix-api"], env, tmp / "api.log"))
        procs.append(start(["uv", "run", "--project", "server", "matrix-worker"], env, tmp / "worker.log"))

        def api_up() -> bool:
            if procs[0].poll() is not None:
                raise RuntimeError(f"matrix-api saiu cedo; veja {tmp / 'api.log'}")
            try:
                return httpx.get(f"{BASE}/health", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        if not poll(api_up, timeout=120):
            raise RuntimeError(f"matrix-api não respondeu; veja {tmp / 'api.log'}")

        admin = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, timeout=30)
        created = admin.post("/api/v1/admin/projects", json={"name": f"rag-demo-{RUN}"})
        created.raise_for_status()
        api_key = created.json()["api_key"]
        http = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)

        def call(method: str, path: str, **kw: Any) -> Any:
            r = http.request(method, path, **kw)
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
            return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text

        # -- setup: rules, online eval (before the traffic exists)
        rules = [
            ("erro -> fila", {"status": "error"}, ["annotate"]),
            ("feedback negativo -> fila + alerta", {"feedback": "negative"}, ["annotate", "alert"]),
            ("faithfulness baixa -> fila + alerta",
             {"score_name": "faithfulness", "op": "<", "value": 0.7}, ["annotate", "alert"]),
        ]
        for name, condition, actions in rules:
            call("POST", "/api/v1/rules", json={"name": name, "condition": condition, "actions": actions})
        call("POST", "/api/v1/online-evals",
             json={"name": "faithfulness", "metric": "faithfulness", "sampling_rate": 1.0})
        print("Regras e avaliação online criadas; rodando o demo...")

        demo = subprocess.run(
            ["uv", "run", "--project", "sdk/python", "python", "examples/rag_demo.py"],
            cwd=ROOT, env={**env, "MATRIX_API_KEY": api_key, "MATRIX_ENDPOINT": BASE},
            capture_output=True, text=True, timeout=300,
        )
        print(demo.stdout.strip())
        if demo.returncode != 0:
            raise RuntimeError(f"demo falhou ({demo.returncode}): {demo.stderr[-500:]}")

        state: dict[str, Any] = {}

        def c1(c: Check) -> None:
            def traces() -> list[dict]:
                items = call("GET", "/api/v1/traces", params={"limit": 100})["items"]
                return items if len(items) >= 8 else []

            items = poll(traces) or call("GET", "/api/v1/traces", params={"limit": 100})["items"]
            state["traces"] = items
            c.expect(len(items) == 8, f"8 traces visíveis (encontrados {len(items)})")
            c.expect(len({t["session_id"] for t in items}) == 3, "3 sessões")
            c.expect(any(t["total_cost_usd"] > 0 for t in items), "custo calculado em algum trace")
            c.expect(any(t["status"] == "error" for t in items), "trace com erro presente")
            deep = [t for t in items if t["span_count"] >= 3 and t["status"] == "ok"]
            c.expect(bool(deep), "trace com árvore multi-span (AGENT + RETRIEVER + LLM)")
            if deep:
                detail = call("GET", f"/api/v1/traces/{deep[0]['trace_id']}")
                spans = detail["spans"]
                kinds = {s["kind"] for s in spans}
                by_id = {s["span_id"] for s in spans}
                children = [s for s in spans if s.get("parent_span_id") in by_id]
                c.expect({"AGENT", "RETRIEVER", "LLM"} <= {str(k).upper() for k in kinds},
                         f"spans com tipos AGENT/RETRIEVER/LLM ({sorted(map(str, kinds))})")
                c.expect(len(children) == len(spans) - 1, "spans ligados em árvore sob um único root")
                llm = next((s for s in spans if str(s["kind"]).upper() == "LLM"), {})
                c.expect((llm.get("cost_usd") or 0) > 0 and (llm.get("input_tokens") or 0) > 0,
                         "span LLM com tokens e custo")
                c.expect(any(e.get("name") == "first_token" for e in llm.get("events", [])), "evento TTFT")
            ok_traces = [t for t in items if t["status"] == "ok"]
            state["ok_traces"] = ok_traces
            c.expect(len(call("GET", "/api/v1/sessions")["items"]) == 3, "GET /sessions lista 3 sessões")

        def c2(c: Check) -> None:
            ds = call("POST", "/api/v1/datasets", json={"name": f"rag-golden-{RUN}"})
            state["dataset"] = ds["id"]
            item = call("POST", f"/api/v1/datasets/{ds['id']}/items/from-span",
                        json={"trace_id": state["ok_traces"][0]["trace_id"]})
            c.expect(bool(item["id"]) and item["source_trace_id"] == state["ok_traces"][0]["trace_id"],
                     "item criado a partir do span (items/from-span)")
            c.expect(bool(item["input"].get("question")), "item com a pergunta como input")
            c.expect(bool(item["expected_output"]) and bool(item["context"]), "item com saída e contexto do retriever")

        def c3(c: Check) -> None:
            ds = state["dataset"]
            for t in state["ok_traces"][1:5]:
                call("POST", f"/api/v1/datasets/{ds}/items/from-span", json={"trace_id": t["trace_id"]})
            got = call("GET", f"/api/v1/datasets/{ds}")
            c.expect(len(got["items"]) >= 4, f"dataset com >= 4 itens ({len(got['items'])})")
            v1 = call("POST", "/api/v1/prompts", json={
                "name": "rag/answer", "type": "text", "labels": ["production"],
                "template": "Answer the support question briefly.\n\nQuestion: {{question}}",
                "commit_message": "v1"})
            v2 = call("POST", "/api/v1/prompts", json={
                "name": "rag/answer", "type": "text", "labels": ["staging"],
                "template": "You are a support agent. Use the context and cite it.\n\nQuestion: {{question}}",
                "commit_message": "v2"})
            c.expect((v1["version"], v2["version"]) == (1, 2), "prompt rag/answer v1 e v2 criados")
            labels = {p["name"]: p["labels"] for p in call("GET", "/api/v1/prompts")["items"]}
            c.expect(labels.get("rag/answer") == {"production": 1, "staging": 2}, f"labels {labels.get('rag/answer')}")
            metrics = [
                {"type": "faithfulness"}, {"type": "answer_relevancy"},
                {"type": "contains"}, {"type": "latency", "params": {"max_ms": 500}},
            ]

            def variant(label: str, version: int, model: str) -> dict:
                return {"label": label, "prompt": {"name": "rag/answer", "version": version},
                        "model": {"provider": "fake", "name": model}}

            base = call("POST", "/api/v1/experiments", json={
                "name": "baseline", "dataset_id": ds, "metrics": metrics,
                "variants": [variant("v1/model-a", 1, "model-a")]})
            done = poll(lambda: (e := call("GET", f"/api/v1/experiments/{base['id']}"))["experiment"]["status"]
                        in ("done", "failed") and e)
            c.expect(bool(done) and done["experiment"]["status"] == "done", "experimento baseline concluído")
            n_items = len(got["items"])
            matrix = call("POST", "/api/v1/experiments", json={
                "name": "matriz 2x2", "dataset_id": ds, "baseline_experiment_id": base["id"], "metrics": metrics,
                "variants": [variant("v1/model-a", 1, "model-a"), variant("v1/model-b", 1, "model-b"),
                             variant("v2/model-a", 2, "model-a"), variant("v2/model-b", 2, "model-b")]})
            res = poll(lambda: (e := call("GET", f"/api/v1/experiments/{matrix['id']}"))["experiment"]["status"]
                       in ("done", "failed") and e)
            c.expect(bool(res) and res["experiment"]["status"] == "done", "experimento 2 prompts x 2 modelos concluído")
            if res:
                results = res["results"]
                c.expect(len(results) == 4 * n_items, f"{len(results)} resultados = 4 variantes x {n_items} itens")
                names = set()
                for r in results:
                    names |= set(r["scores"])
                c.expect({"faithfulness", "answer_relevancy", "contains", "latency"} <= names,
                         f"métricas calculadas: {sorted(names)}")
                c.expect(isinstance(res["regressions"], list), f"regressões calculadas ({len(res['regressions'])} células)")

        def c4(c: Check) -> None:
            def annotations() -> list[dict]:
                items = call("GET", "/api/v1/annotations", params={"limit": 100})["items"]
                reasons = {a["reason"] for a in items}
                need = {f"rule:{n}" for n, _, _ in rules}
                return items if need <= reasons else []

            items = poll(annotations) or call("GET", "/api/v1/annotations", params={"limit": 100})["items"]
            reasons = sorted({a["reason"] for a in items if a["reason"]})
            for name, _, _ in rules:
                c.expect(f"rule:{name}" in reasons, f"anotação criada pela regra '{name}'")
            target = next((a for a in items if a["reason"] == "rule:faithfulness baixa -> fila + alerta"), None)
            if target is None:
                c.expect(False, "anotação de faithfulness para completar")
                return
            fixed = "Support is open Monday to Friday from 9am to 6pm."
            call("POST", f"/api/v1/annotations/{target['id']}/complete", json={
                "scores": [{"name": "human_quality", "value": 1.0}], "corrected_output": fixed,
                "add_to_dataset_id": state["dataset"], "comment": "resposta contradizia o contexto"})
            dpo = call("GET", "/api/v1/exports/dpo.jsonl")
            lines = [ln for ln in dpo.splitlines() if ln.strip()]
            import json
            pairs = [json.loads(ln) for ln in lines]
            pair = next((p for p in pairs if p["trace_id"] == target["trace_id"]), None)
            c.expect(pair is not None and pair["chosen"] == fixed and "24 hours" in pair["rejected"],
                     "par DPO exportado (chosen = correção, rejected = saída original)")
            ds = call("GET", f"/api/v1/datasets/{state['dataset']}")
            added = [i for i in ds["items"] if i["source_trace_id"] == target["trace_id"]]
            # The trace may already be in the dataset (from-span in criterion 3); look for the corrected copy.
            c.expect(any(i["expected_output"] == fixed for i in added),
                     "correção entrou no dataset como caso de regressão")
            done = call("GET", "/api/v1/annotations", params={"status": "done"})["items"]
            c.expect(any(a["id"] == target["id"] for a in done), "anotação marcada como concluída")

        def c5(c: Check) -> None:
            def scored() -> list[dict]:
                out = []
                for t in state["traces"]:
                    out += [s for s in call("GET", f"/api/v1/traces/{t['trace_id']}/scores") if s["name"] == "faithfulness"]
                return out if len(out) >= 6 else []

            scores = poll(scored)
            if not scores:
                scores = scored.__call__() or []
            c.expect(len(scores) >= 6, f"avaliação online gravou scores de faithfulness ({len(scores)})")
            c.expect(all(s["source"] == "llm" for s in scores), "scores com source=llm")
            c.expect(any(s["value"] < 0.7 for s in scores), "algum score abaixo de 0.7 (resposta contraditória)")
            alerts = poll(lambda: call("GET", "/api/v1/alerts")["items"])
            c.expect(bool(alerts), f"alertas disparados ({len(alerts or [])})")
            c.expect(any("faithfulness" in a["message"] for a in alerts or []), "alerta da regra de faithfulness")

        def c6(c: Check) -> None:
            c.expect(True, "rode `make test` (server, sdk e web) para a suíte completa; `make demo` repete esta verificação")

        criteria = [
            ("1. Traces com spans, custo e árvore de agente", c1),
            ("2. Span vira item de dataset", c2),
            ("3. Matriz 2 prompts x 2 modelos com regressões", c3),
            ("4. Regras -> fila -> correção -> DPO + dataset", c4),
            ("5. Avaliação online + alerta", c5),
            ("6. make test", c6),
        ]
        for title, fn in criteria:
            check = Check()
            try:
                fn(check)
            except Exception as exc:  # noqa: BLE001 - report the failure and keep going
                check.expect(False, f"exceção: {type(exc).__name__}: {exc}")
                traceback.print_exc()
            results.append((title, check.ok, check.notes))
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO de infraestrutura: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        for p in procs:
            stop(p)
    print()
    for title, ok, notes in results:
        print(f"{'PASS' if ok else 'FAIL'}  {title}")
        for n in notes:
            print(f"        {n}")
    all_ok = bool(results) and all(ok for _, ok, _ in results)
    print("\nMVP:", "TODOS OS CRITÉRIOS PASSARAM" if all_ok else "HÁ CRITÉRIOS FALHANDO")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
