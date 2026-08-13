import json

from financial_agent import connect, get_preferences, init_db, set_preference
from agent import AgentRunner, ToolCall, ToolExecutionContext
from agent.context import context_for_prompt, load_agent_context
from agent.tools import build_tool_registry
from services.personal_memory import PERSONAL_MEMORY_PREFERENCE_KEY


def test_agent_memory_tool_only_writes_explicit_personal_memory(tmp_path):
    conn = connect(tmp_path / "ledger.db")
    init_db(conn)
    result = AgentRunner(build_tool_registry()).run(
        [
            ToolCall(
                name="remember_personal_preference",
                arguments={
                    "title": "默认支付账户",
                    "content": "日常消费未说明账户时优先微信。",
                },
            )
        ],
        ToolExecutionContext(state=conn, run_id="memory-test"),
    )[0]

    assert result.ok is True
    assert result.output["memory"]["source"] == "agent"
    assert get_preferences(conn)[PERSONAL_MEMORY_PREFERENCE_KEY][0]["title"] == "默认支付账户"
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_only_enabled_personal_memories_reach_model_context(tmp_path):
    conn = connect(tmp_path / "ledger.db")
    init_db(conn)
    set_preference(
        conn,
        PERSONAL_MEMORY_PREFERENCE_KEY,
        [
            {"id": "on", "title": "默认账户", "content": "优先微信", "enabled": True},
            {"id": "off", "title": "旧规则", "content": "不要使用", "enabled": False},
        ],
    )

    prompt_context = json.loads(
        context_for_prompt(load_agent_context(conn, "memory"), "今天午饭 20 元")
    )
    assert prompt_context["personal_memories"] == [
        {"title": "默认账户", "content": "优先微信"}
    ]
