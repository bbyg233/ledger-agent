from types import SimpleNamespace

from agent.usage import extract_model_usage
from financial_agent import connect, init_db, list_agent_runs, log_agent_run
from ledger.observability import SQLiteAgentObserver


def test_extracts_chat_and_responses_token_usage():
    chat = SimpleNamespace(
        id="chat-1",
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            prompt_tokens_details=SimpleNamespace(cached_tokens=20),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=8),
        ),
    )
    responses = SimpleNamespace(
        id="resp-1",
        usage=SimpleNamespace(
            input_tokens=80,
            output_tokens=25,
            total_tokens=105,
            input_tokens_details=SimpleNamespace(cached_tokens=10),
            output_tokens_details=SimpleNamespace(reasoning_tokens=5),
        ),
    )

    chat_usage = extract_model_usage(chat, api_style="chat", purpose="test", model="m")
    responses_usage = extract_model_usage(
        responses, api_style="responses", purpose="test", model="m"
    )

    assert (chat_usage.input_tokens, chat_usage.output_tokens) == (120, 30)
    assert (chat_usage.cached_tokens, chat_usage.reasoning_tokens) == (20, 8)
    assert (responses_usage.input_tokens, responses_usage.output_tokens) == (80, 25)
    assert (responses_usage.cached_tokens, responses_usage.reasoning_tokens) == (10, 5)


def test_sqlite_observer_persists_checkpoint_and_aggregates_cost(tmp_path):
    conn = connect(tmp_path / "ledger.db")
    init_db(conn)
    observer = SQLiteAgentObserver(
        conn,
        run_id="run-observe",
        session_id="web",
        provider="relay",
        model="demo-model",
        api_style="chat",
        pricing={
            "demo-model": {
                "input_per_million": 2,
                "output_per_million": 8,
                "cached_input_per_million": 1,
            }
        },
    )
    usage = extract_model_usage(
        SimpleNamespace(
            id="chat-usage",
            usage=SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=500,
                total_tokens=1500,
                prompt_tokens_details=SimpleNamespace(cached_tokens=200),
            ),
        ),
        api_style="chat",
        purpose="native_agent",
        model="demo-model",
        duration_ms=37,
    )

    observer.record_usage(usage)
    observer.save_checkpoint(
        {"api_style": "chat", "model_calls": 1, "executed_steps": 1, "messages": []}
    )
    assert observer.load_checkpoint()["executed_steps"] == 1
    observer.complete_checkpoint()
    assert observer.load_checkpoint() is None

    log_agent_run(
        conn,
        run_id="run-observe",
        tool_mode="native",
        session_id="web",
        provider="relay",
        model="demo-model",
        action="summary",
        status="success",
        duration_ms=10,
        input_chars=4,
    )
    run = list_agent_runs(conn, limit=1)[0]
    assert run["model_requests"] == 1
    assert run["total_tokens"] == 1500
    assert run["estimated_cost"] == 0.0058
    assert run["model_calls"][0]["response_id"] == "chat-usage"
    assert run["model_calls"][0]["duration_ms"] == 37
