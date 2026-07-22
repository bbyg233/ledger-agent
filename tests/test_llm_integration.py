import os

import pytest

from financial_agent import (
    AgentAction,
    TransactionDraft,
    add_transaction,
    connect,
    execute_native_agent,
    init_db,
    load_environment,
    load_agent_context,
    parse_transaction_with_llm,
    route_agent_action,
    set_llm_selection,
)


pytestmark = pytest.mark.integration
load_environment(override=True)


def require_api_key() -> None:
    if not os.environ.get("LEDGER_AGENT_API_KEY"):
        pytest.skip("需要在 .env 中配置 LLM API key")


def native_fixture(tmp_path, monkeypatch, provider, model):
    monkeypatch.setenv("LEDGER_AGENT_SETTINGS", str(tmp_path / "settings.json"))
    set_llm_selection(provider, model)
    conn = connect(tmp_path / "native.db")
    init_db(conn)
    for transaction_date, amount, merchant in (
        ("2026-06-08", 20, "面馆"),
        ("2026-07-08", 20, "面馆"),
        ("2026-07-09", 40, "咖啡店"),
    ):
        add_transaction(
            conn,
            TransactionDraft(
                date=transaction_date,
                amount=amount,
                direction="expense",
                category="餐饮",
                account="微信",
                merchant=merchant,
                note="",
                raw_text=merchant,
            ),
        )
    return conn, load_agent_context(conn, "native-test")


def test_live_llm_parses_chinese_transaction():
    require_api_key()

    draft = parse_transaction_with_llm("昨天晚饭和朋友吃火锅 168.5 元 微信")

    assert draft.amount == 168.5
    assert draft.direction == "expense"
    assert draft.account == "微信"
    assert draft.category


def test_live_llm_routes_and_parses_transaction_in_one_call():
    require_api_key()

    action = route_agent_action("帮我记一笔，昨天午饭 38 元 微信")

    assert action.action == "record"
    assert action.transaction is not None
    assert action.transaction["amount"] == 38
    assert action.transaction["direction"] == "expense"
    assert action.transaction["account"] == "微信"


def test_live_volcengine_routes_and_parses_transaction(tmp_path, monkeypatch):
    if not os.environ.get("ARK_API_KEY"):
        pytest.skip("需要在 .env 中配置 ARK_API_KEY")
    monkeypatch.setenv("LEDGER_AGENT_SETTINGS", str(tmp_path / "settings.json"))
    set_llm_selection("volcengine", "glm-5-2-260617")

    action = route_agent_action("帮我记一笔，今天微信花 5.5 元买电池")

    assert action.action == "record"
    assert action.transaction is not None
    assert action.transaction["amount"] == 5.5
    assert action.transaction["direction"] == "expense"
    assert action.transaction["account"] == "微信"
    assert "电池" in action.transaction["merchant"]
    assert "电池" not in action.transaction["note"]


def test_live_volcengine_doubao_lite_responses_api(tmp_path, monkeypatch):
    if not os.environ.get("ARK_API_KEY"):
        pytest.skip("需要在 .env 中配置 ARK_API_KEY")
    monkeypatch.setenv("LEDGER_AGENT_SETTINGS", str(tmp_path / "settings.json"))
    set_llm_selection("volcengine", "doubao-seed-2-0-lite-260428")

    action = route_agent_action("帮我记一笔，今天支付宝买咖啡 22 元")

    assert action.action == "record"
    assert action.transaction is not None
    assert action.transaction["amount"] == 22
    assert action.transaction["account"] == "支付宝"
    assert "咖啡" in action.transaction["merchant"]


def test_live_volcengine_creates_multiple_drafts(tmp_path, monkeypatch):
    if not os.environ.get("ARK_API_KEY"):
        pytest.skip("需要在 .env 中配置 ARK_API_KEY")
    monkeypatch.setenv("LEDGER_AGENT_SETTINGS", str(tmp_path / "settings.json"))
    set_llm_selection("volcengine", "glm-5-2-260617")

    action = route_agent_action("今天午饭30元，地铁4元，咖啡20元，都是微信支付")

    assert action.action == "record"
    assert len(action.transactions) == 3
    assert [item["amount"] for item in action.transactions] == [30, 4, 20]
    assert all(item["account"] == "微信" for item in action.transactions)


def test_live_llm_routes_spending_trend_analysis():
    require_api_key()

    action = route_agent_action("分析最近三个月为什么餐饮开支变多")

    assert action.action == "analyze"
    assert action.category == "餐饮"
    assert action.periods == 3


@pytest.mark.parametrize(
    ("provider", "model", "api_key_name"),
    [
        ("volcengine", "glm-5-2-260617", "ARK_API_KEY"),
        ("volcengine", "doubao-seed-2-0-lite-260428", "ARK_API_KEY"),
        ("relay", "deepseek-v4-flash", "LEDGER_AGENT_API_KEY"),
    ],
)
def test_live_native_tool_calling(tmp_path, monkeypatch, provider, model, api_key_name):
    if not os.environ.get(api_key_name):
        pytest.skip(f"需要在 .env 中配置 {api_key_name}")
    conn, context = native_fixture(tmp_path, monkeypatch, provider, model)

    action, result = execute_native_agent(
        conn,
        "分析2026年6月到7月餐饮支出的变化",
        context,
        run_id=f"native-{provider}-{model}",
        session_id="native-test",
        dry_run=True,
        assume_yes=False,
        allow_interactive_approval=False,
    )

    assert action.action in {"analyze", "summary", "search", "where"}
    assert result["native_answer"]
    assert result["tools_used"]


def test_live_native_responses_combines_two_tools(tmp_path, monkeypatch):
    if not os.environ.get("ARK_API_KEY"):
        pytest.skip("需要在 .env 中配置 ARK_API_KEY")
    conn, context = native_fixture(
        tmp_path, monkeypatch, "volcengine", "doubao-seed-2-0-lite-260428"
    )

    _, result = execute_native_agent(
        conn,
        "先搜索2026年7月咖啡相关账单，再按商户汇总2026年7月支出，结合两个结果回答。",
        context,
        run_id="native-two-tools",
        session_id="native-test",
        dry_run=True,
        assume_yes=False,
        allow_interactive_approval=False,
    )

    assert {"search_ledger", "aggregate_spending"}.issubset(result["tools_used"])
    steps = conn.execute(
        "SELECT tool_name FROM agent_steps WHERE run_id = ? ORDER BY step_index",
        ("native-two-tools",),
    ).fetchall()
    assert len(steps) >= 2


def test_live_native_record_stays_a_draft(tmp_path, monkeypatch):
    if not os.environ.get("ARK_API_KEY"):
        pytest.skip("需要在 .env 中配置 ARK_API_KEY")
    conn, context = native_fixture(
        tmp_path, monkeypatch, "volcengine", "doubao-seed-2-0-lite-260428"
    )
    before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    action, result = execute_native_agent(
        conn,
        "今天微信买早餐12元",
        context,
        run_id="native-record-preview",
        session_id="native-test",
        dry_run=True,
        assume_yes=False,
        allow_interactive_approval=False,
    )

    after = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert action.action == "record"
    assert result["drafts"][0]["amount"] == 12
    assert before == after


@pytest.mark.parametrize(
    ("text", "expected_action"),
    [
        ("帮我找一下咖啡相关的消费", "search"),
        ("我这个月的钱主要花在哪里了", "where"),
        ("给我生成这个月的复盘报告", "report"),
        ("本月收入一万五，想存五千，帮我规划", "plan"),
    ],
)
def test_live_llm_routes_financial_intents(text: str, expected_action: str):
    require_api_key()

    action = route_agent_action(text)

    assert isinstance(action, AgentAction)
    assert action.action == expected_action
