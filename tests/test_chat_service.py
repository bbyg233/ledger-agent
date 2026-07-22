from agent.models import AgentAction
import financial_agent as fa
from services.chat import process_chat_request


def test_pending_chat_request_can_resume_from_saved_user_message(tmp_path, monkeypatch):
    database = tmp_path / "ledger.db"
    settings = tmp_path / "settings.json"
    monkeypatch.setenv("LEDGER_AGENT_DB", str(database))
    monkeypatch.setenv("LEDGER_AGENT_SETTINGS", str(settings))
    monkeypatch.setenv("LEDGER_AGENT_PROVIDER", "relay")
    monkeypatch.setenv("LEDGER_AGENT_MODEL", "deepseek-v4-flash")
    conn = fa.connect()
    fa.init_db(conn)
    fa.create_chat_request(
        conn,
        "resume_request_1234",
        "web",
        "查看本月汇总",
        "relay",
        "deepseek-v4-flash",
    )
    conn.close()
    captured = {}

    def execute(conn, text, context, **kwargs):
        captured["text"] = text
        captured["run_id"] = kwargs["run_id"]
        action = AgentAction(action="summary", text=text, month="2026-07")
        return action, {
            "summary": {"month": "2026-07", "income": 0, "expense": 0, "net": 0},
            "tool_mode_used": "native",
        }

    monkeypatch.setattr(fa, "execute_agent_request", execute)
    result = process_chat_request(
        request_id="resume_request_1234",
        session_id="",
        text="",
        resume=True,
    )

    conn = fa.connect()
    request = fa.get_chat_request(conn, "resume_request_1234")
    messages = fa.recent_messages(conn, "web", limit=10)
    conn.close()
    assert result["kind"] == "result"
    assert captured == {"text": "查看本月汇总", "run_id": "resume_request_1234"}
    assert request["status"] == "completed"
    assert [message["role"] for message in messages] == ["user", "assistant"]
