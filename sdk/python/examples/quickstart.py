"""Quickstart: an agent-ish flow traced with MATRIX helpers.

    MATRIX_API_KEY=mx_... MATRIX_ENDPOINT=http://localhost:8000 uv run python examples/quickstart.py
"""

import matrix_sdk as mx

client = mx.init(project_name="demo", service_name="quickstart")


@mx.trace_tool("search_kb")
def search_kb(query: str) -> list[str]:
    return ["MATRIX ingests OTLP traces."]


def answer(question: str) -> str:
    with mx.trace_agent("support-agent") as agent:
        agent.set_input(question)
        with mx.trace_retriever("kb") as r:
            r.set_input(question)
            r.set_documents([{"id": "d1", "content": "MATRIX ingests OTLP traces.", "score": 0.92}])
        search_kb(question)
        with mx.trace_llm("answer", model="gpt-4o-mini", provider="openai") as llm:
            llm.set_messages(input=[{"role": "user", "content": question}])
            llm.mark_first_token()
            text = "MATRIX receives traces over OTLP/HTTP."
            llm.set_messages(output=[{"role": "assistant", "content": text}])
            llm.set_tokens(prompt=42, completion=9)
        agent.set_output(text)
        trace_id = mx.current_trace_id()
    if trace_id:
        client.score(trace_id, "helpfulness", 1.0, comment="quickstart")
    return text


if __name__ == "__main__":
    with mx.session("demo-session", user_id="user-1"):
        print(answer("What does MATRIX ingest?"))
    client.shutdown()
