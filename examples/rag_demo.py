"""Demo do MATRIX: um agente de suporte com RAG simulado, instrumentado só com o SDK.

Gera 3 sessões e 8 turnos: AGENT -> RETRIEVER -> (TOOL) -> LLM. Entre eles há um turno com erro
em uma ferramenta, um turno cuja resposta contradiz o contexto (para a faithfulness sinalizar) e um
feedback negativo do usuário em um trace.

Uso (servidor em http://localhost:8000, ou defina MATRIX_ENDPOINT):

    MATRIX_API_KEY=mtx_... uv run --project sdk/python python examples/rag_demo.py

Nenhuma chamada externa: o "LLM" e as ferramentas são simulados.
"""

from __future__ import annotations

import os
import sys
import time

import matrix_sdk as matrix

KB = {
    "refund": "Refunds are issued to the original payment method within 5 business days after approval.",
    "shipping": "Standard shipping takes 3 to 7 business days and every order includes a tracking number.",
    "password": "Reset your password from the login page by choosing Forgot password and following the email link.",
    "warranty": "All devices include a 12 month warranty covering manufacturing defects.",
    "hours": "Support is open Monday to Friday from 9am to 6pm.",
    "invoice": "Invoices are available in the billing page and can be downloaded as PDF files.",
    "cancel": "You can cancel your plan at any time from the billing page and keep access until the period ends.",
}

# (session, user, question, kb key, tool, answer, scenario)
TURNS = [
    ("sess-ana", "ana", "How long do refunds take?", "refund", None, KB["refund"], "ok"),
    ("sess-ana", "ana", "When will my order arrive?", "shipping", "lookup_order", KB["shipping"], "ok"),
    ("sess-ana", "ana", "I forgot my password, what now?", "password", None, KB["password"], "feedback"),
    ("sess-bruno", "bruno", "Does my laptop have a warranty?", "warranty", "lookup_order", KB["warranty"], "ok"),
    ("sess-bruno", "bruno", "Is the tablet in stock?", "warranty", "check_inventory", "", "tool_error"),
    ("sess-bruno", "bruno", "When is support open?", "hours", None,
     "Support is open 24 hours every day including holidays and weekends.", "contradiction"),
    ("sess-carla", "carla", "Where do I find my invoice?", "invoice", None, KB["invoice"], "ok"),
    ("sess-carla", "carla", "How can I cancel my plan?", "cancel", "lookup_order", KB["cancel"], "ok"),
]


def run_tool(name: str, scenario: str) -> str:
    with matrix.trace_tool(name) as tool:
        tool.set_input({"tool": name})
        time.sleep(0.01)
        if scenario == "tool_error":
            raise TimeoutError("inventory service did not answer within 2s")
        result = {"status": "ok", "tool": name}
        tool.set_output(result)
        return str(result)


def answer_turn(question: str, kb_key: str, tool: str | None, answer: str, scenario: str) -> str | None:
    """Returns the trace id."""
    with matrix.trace_agent("support-agent") as agent:
        trace_id = agent.trace_id
        agent.set_input({"question": question})
        with matrix.trace_retriever("kb-search") as retriever:
            retriever.set_input(question)
            docs = [
                {"id": kb_key, "content": KB[kb_key], "score": 0.92},
                {"id": "faq", "content": "Contact us by email for anything not covered here.", "score": 0.41},
            ]
            retriever.set_documents(docs)
        tool_note = run_tool(tool, scenario) if tool else None
        context = "\n".join(d["content"] for d in docs)
        system = "Answer using only the context.\n" + context
        messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
        with matrix.trace_llm("generate-answer", model="gpt-4o-mini", provider="openai") as llm:
            llm.set_messages(input=messages)
            time.sleep(0.02)
            llm.mark_first_token()  # TTFT
            time.sleep(0.03)
            llm.set_messages(input=messages, output=[{"role": "assistant", "content": answer}])
            llm.set_tokens(prompt=len(system.split()) + len(question.split()) + (len(tool_note.split()) if tool_note else 0),
                           completion=len(answer.split()))
        agent.set_output(answer)
    return trace_id


def main() -> int:
    api_key = os.environ.get("MATRIX_API_KEY")
    if not api_key:
        print("Defina MATRIX_API_KEY (e MATRIX_ENDPOINT, se não for http://localhost:8000).", file=sys.stderr)
        return 2
    client = matrix.init(service_name="support-rag-demo", batch=False)
    feedback_trace: str | None = None
    for session_id, user, question, kb_key, tool, answer, scenario in TURNS:
        with matrix.session(session_id, user_id=user):
            try:
                trace_id = answer_turn(question, kb_key, tool, answer, scenario)
            except TimeoutError as exc:
                print(f"[{session_id}] erro esperado na ferramenta: {exc}")
                continue
        if scenario == "feedback":
            feedback_trace = trace_id
        print(f"[{session_id}] {scenario:13s} trace={trace_id}")
    client.flush()
    if feedback_trace:
        # The trace must be stored before the score is accepted; the ingest call above is synchronous.
        client.score(feedback_trace, "user_feedback", 0.0, label="negative", comment="thumbs down", source="human")
        print(f"feedback negativo enviado para {feedback_trace}")
    client.shutdown()
    print("Demo concluída: 3 sessões, 8 turnos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
