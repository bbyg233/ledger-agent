import json
from datetime import date as calendar_date
from types import SimpleNamespace

from fastapi.testclient import TestClient

from web_app import app


client = TestClient(app)


class FakeNativeMessage:
    def __init__(self, *, tool_calls=None, content=""):
        self.tool_calls = tool_calls or []
        self.content = content

    def model_dump(self, exclude_none=True):
        return {
            "role": "assistant",
            "content": self.content,
            "tool_calls": [
                {
                    "id": item.id,
                    "type": "function",
                    "function": {
                        "name": item.function.name,
                        "arguments": item.function.arguments,
                    },
                }
                for item in self.tool_calls
            ],
        }


def mock_native_tool(monkeypatch, name, arguments, final_text="处理完成。"):
    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=FakeNativeMessage(
                        tool_calls=[
                            SimpleNamespace(
                                id="test-call-1",
                                function=SimpleNamespace(
                                    name=name,
                                    arguments=json.dumps(arguments, ensure_ascii=False),
                                ),
                            )
                        ]
                    )
                )
            ]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(message=FakeNativeMessage(content=final_text))]
        ),
    ]

    def create(**kwargs):
        return responses.pop(0)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr("financial_agent.llm_client", lambda: client)
    monkeypatch.setattr("financial_agent.llm_uses_responses_api", lambda model: False)


def test_agent_tool_catalog_exposes_schemas_and_risk_metadata():
    response = client.get("/api/agent/tools")

    assert response.status_code == 200
    tools = {item["name"]: item for item in response.json()["tools"]}
    assert set(tools) == {
        "ask_clarification",
        "record_transactions",
        "get_month_summary",
        "create_budget_plan",
        "search_ledger",
        "aggregate_spending",
        "analyze_spending_trend",
        "compare_spending_periods",
        "find_recurring_expenses",
        "get_account_balances",
        "get_subscriptions",
        "get_liabilities",
        "propose_account_transfer",
        "propose_subscriptions",
        "propose_subscription_charge",
        "propose_subscription_skip",
        "propose_liability_statement",
        "propose_liability_payment",
        "propose_liability_charge",
        "generate_monthly_report",
    }
    assert tools["record_transactions"]["risk"] == "write"
    assert tools["record_transactions"]["requires_confirmation"] is True
    record_schema = tools["record_transactions"]["input_schema"]
    assert record_schema["required"] == ["transactions"]
    assert "repayment" in tools["search_ledger"]["input_schema"]["properties"]["direction"]["enum"]
    transaction_schema = record_schema["$defs"]["TransactionToolInput"]
    assert "raw_text" not in transaction_schema["properties"]
    assert "classification_source" not in transaction_schema["properties"]
    assert tools["analyze_spending_trend"]["input_schema"]["properties"]["periods"]
    assert tools["compare_spending_periods"]["input_schema"]["properties"]["baseline_end"]
    assert tools["find_recurring_expenses"]["input_schema"]["properties"]["min_occurrences"]
    assert tools["propose_subscriptions"]["requires_confirmation"] is True
    assert tools["propose_liability_payment"]["risk"] == "write"
    assert tools["propose_liability_charge"]["requires_confirmation"] is True


def test_agent_management_drafts_require_confirmation_before_any_write(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    mock_native_tool(
        monkeypatch,
        "propose_subscriptions",
        {
            "subscriptions": [
                {
                    "name": "视频会员", "amount": 25, "cycle_months": 1,
                    "next_charge_date": "2026-07-20", "category": "娱乐", "account": "微信",
                }
            ]
        },
    )

    draft_response = client.post(
        "/api/chat", json={"text": "帮我建立一个每月 20 日扣 25 元的视频会员", "session_id": "management"}
    )
    assert draft_response.status_code == 200, draft_response.text
    draft_payload = draft_response.json()
    before = client.get("/api/subscriptions", params={"month": "2026-07"})
    confirmed = client.post(
        "/api/management-proposals/confirm",
        json={
            "request_id": draft_payload["request_id"],
            "proposals": draft_payload["proposals"],
        },
    )
    after = client.get("/api/subscriptions", params={"month": "2026-07"})

    assert draft_payload["kind"] == "management_drafts"
    assert before.json()["items"] == []
    assert confirmed.json()["applied"] == 1
    assert after.json()["items"][0]["name"] == "视频会员"


def test_management_draft_removal_and_individual_confirmation_persist_on_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    mock_native_tool(
        monkeypatch,
        "propose_subscriptions",
        {
            "subscriptions": [
                {
                    "name": "视频会员", "amount": 25, "cycle_months": 1,
                    "next_charge_date": "2026-07-20", "category": "娱乐", "account": "微信",
                },
                {
                    "name": "音乐会员", "amount": 15, "cycle_months": 1,
                    "next_charge_date": "2026-07-25", "category": "娱乐", "account": "支付宝",
                },
            ]
        },
    )
    draft_response = client.post(
        "/api/chat", json={"text": "建立两个订阅", "session_id": "persist-drafts"}
    ).json()
    request_id = draft_response["request_id"]
    original = draft_response["proposals"]

    removed = client.patch(
        f"/api/chat/requests/{request_id}/management-proposals",
        json={"proposals": [original[1]]},
    )
    restored_after_removal = client.get(f"/api/chat/requests/{request_id}").json()
    confirmed = client.post(
        "/api/management-proposals/confirm",
        json={
            "request_id": request_id,
            "proposals": [original[1]],
            "remaining_proposals": [],
            "complete_request": True,
        },
    )
    completed = client.get(f"/api/chat/requests/{request_id}").json()

    assert removed.status_code == 200
    assert [item["draft"]["name"] for item in restored_after_removal["result"]["proposals"]] == ["音乐会员"]
    assert confirmed.status_code == 200
    assert confirmed.json()["applied"] == 1
    assert completed["status"] == "completed"
    assert client.get("/api/subscriptions", params={"month": "2026-07"}).json()["items"][0]["name"] == "音乐会员"


def test_agent_payment_draft_updates_existing_liability_only_after_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    liability = client.post(
        "/api/liabilities",
        json={
            "name": "花呗", "provider": "支付宝", "kind": "consumer_credit",
            "due_amount": 200, "due_date": "2026-07-20",
            "minimum_payment": 20, "repayment_account": "银行卡",
        },
    ).json()["liability"]
    mock_native_tool(
        monkeypatch,
        "propose_liability_payment",
        {
            "liability_id": liability["id"], "statement_month": "2026-07",
            "amount": 80, "paid_at": "2026-07-12", "note": "已还",
        },
    )

    draft_response = client.post("/api/chat", json={"text": "我刚还了花呗 80", "session_id": "payment"})
    before = client.get("/api/liabilities", params={"month": "2026-07"})
    confirmed = client.post(
        "/api/management-proposals/confirm",
        json={"request_id": draft_response.json()["request_id"], "proposals": draft_response.json()["proposals"]},
    )

    assert draft_response.json()["kind"] == "management_drafts"
    assert before.json()["items"][0]["due_amount"] == 200
    confirmed_liability = confirmed.json()["results"][0]["liability"]
    assert confirmed_liability["due_amount"] == 200
    assert confirmed_liability["remaining_amount"] == 120


def test_subscription_and_liability_endpoints_keep_charge_and_repayment_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    subscription = client.post(
        "/api/subscriptions",
        json={
            "name": "视频会员", "amount": 25, "cycle_months": 1,
            "next_charge_date": "2026-07-10", "category": "娱乐", "account": "微信",
        },
    )
    liability = client.post(
        "/api/liabilities",
        json={
            "name": "信用卡", "provider": "银行", "kind": "credit_card",
            "due_amount": 400, "due_date": "2026-07-15",
            "minimum_payment": 40, "repayment_account": "银行卡",
        },
    )
    charged = client.post(f"/api/subscriptions/{subscription.json()['subscription']['id']}/charge")
    paid = client.post(
        f"/api/liabilities/{liability.json()['liability']['id']}/payments",
        json={"amount": 100, "paid_at": "2026-07-12", "statement_month": "2026-07"},
    )
    dashboard = client.get("/api/dashboard", params={"month": "2026-07"})
    financial_records = client.get("/api/financial-records", params={"month": "2026-07"}).json()
    repayment_records = client.get(
        "/api/financial-records",
        params={"month": "2026-07", "direction": "repayment", "query": "信用卡"},
    ).json()

    assert charged.status_code == 200
    assert paid.status_code == 200
    paid_liability = paid.json()["payment"]["liability"]
    assert paid_liability["due_amount"] == 400
    assert paid_liability["remaining_amount"] == 300
    assert dashboard.json()["summary"]["expense"] == 25
    assert {item["direction"] for item in financial_records["results"]} == {"expense", "repayment", "liability"}
    assert len(repayment_records["results"]) == 1
    assert repayment_records["results"][0]["statement_month"] == "2026-07"
    assert dashboard.json()["forecast"] == {
        "scheduled_subscriptions": 0.0,
        "liability_due": 400.0,
        "liability_paid": 100.0,
        "liability_remaining": 300.0,
        "current_debt": 300.0,
        "repayment_outflow": 100.0,
        "cash_change": -125.0,
    }


def test_web_can_correct_and_reverse_liability_payment(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    liability = client.post(
        "/api/liabilities",
        json={
            "name": "信用卡", "kind": "credit_card", "statement_month": "2026-07",
            "due_amount": 300, "due_date": "2026-07-20", "repayment_account": "银行卡",
        },
    ).json()["liability"]
    paid = client.post(
        f"/api/liabilities/{liability['id']}/payments",
        json={
            "amount": 120, "paid_at": "2026-07-10", "statement_month": "2026-07",
            "account": "银行卡", "note": "原记录",
        },
    ).json()["payment"]
    payment_id = paid["payment_id"]

    updated = client.patch(
        f"/api/liability-payments/{payment_id}",
        json={"amount": 100, "paid_at": "2026-08-02", "account": "微信", "note": "修正"},
    )
    july = client.get("/api/liabilities", params={"month": "2026-07"}).json()
    august_records = client.get(
        "/api/financial-records", params={"month": "2026-08", "direction": "repayment"}
    ).json()

    assert updated.status_code == 200
    assert updated.json()["payment"]["account"] == "微信"
    assert july["summary"]["paid_amount"] == 100
    assert july["summary"]["remaining_amount"] == 200
    assert august_records["results"][0]["id"] == payment_id
    assert august_records["results"][0]["account"] == "微信"

    removed = client.delete(f"/api/liability-payments/{payment_id}")
    restored = client.get("/api/liabilities", params={"month": "2026-07"}).json()
    logs = client.get("/api/logs/operations").json()["logs"]

    assert removed.status_code == 200
    assert restored["summary"]["paid_amount"] == 0
    assert restored["summary"]["remaining_amount"] == 300
    assert logs[0]["label"] == "撤销还款"
    assert any(item["label"] == "修改还款" for item in logs)


def test_web_subscription_charge_requires_special_reverse(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    today = calendar_date.today().isoformat()
    subscription = client.post(
        "/api/subscriptions",
        json={
            "name": "音乐会员", "amount": 18, "cycle_months": 1,
            "next_charge_date": today, "category": "娱乐", "account": "支付宝",
        },
    ).json()["subscription"]
    charged = client.post(f"/api/subscriptions/{subscription['id']}/charge").json()["charged"]
    transaction_id = charged["transaction_id"]

    generic_delete = client.delete(f"/api/transactions/{transaction_id}")
    reversed_charge = client.delete(f"/api/subscription-charges/{transaction_id}")
    subscriptions = client.get(
        "/api/subscriptions", params={"month": today[:7], "include_inactive": True}
    ).json()
    records = client.get("/api/financial-records", params={"month": today[:7]}).json()
    logs = client.get("/api/logs/operations").json()["logs"]

    assert generic_delete.status_code == 400
    assert reversed_charge.status_code == 200
    assert reversed_charge.json()["reversed"]["subscription"]["next_charge_date"] == today
    assert subscriptions["items"][0]["charge_count"] == 0
    assert records["results"] == []
    assert logs[0]["label"] == "撤销订阅扣款"


def test_dashboard_separates_payment_month_from_statement_month(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    liability = client.post(
        "/api/liabilities",
        json={
            "name": "八月花呗", "provider": "支付宝", "kind": "consumer_credit",
            "statement_month": "2026-08", "due_amount": 205, "due_date": "2026-08-20",
        },
    ).json()["liability"]
    paid = client.post(
        f"/api/liabilities/{liability['id']}/payments",
        json={"amount": 205, "paid_at": "2026-07-16", "statement_month": "2026-08"},
    )

    july = client.get("/api/dashboard", params={"month": "2026-07"}).json()
    august = client.get("/api/dashboard", params={"month": "2026-08"}).json()
    august_liabilities = client.get("/api/liabilities", params={"month": "2026-08"}).json()

    assert paid.status_code == 200
    assert july["forecast"]["liability_paid"] == 0
    assert july["forecast"]["repayment_outflow"] == 205
    assert july["forecast"]["cash_change"] == -205
    recent_payment = next(
        item for item in july["recent"] if item["record_type"] == "liability_payment"
    )
    assert recent_payment == {
        "id": paid.json()["payment"]["payment_id"],
        "date": "2026-07-16",
        "amount": 205.0,
        "direction": "repayment",
        "category": "还款",
        "account": "未指定",
        "merchant": "八月花呗",
        "note": "",
        "statement_month": "2026-08",
        "record_type": "liability_payment",
        "liability_id": liability["id"],
        "source": "liability_payment",
        "source_id": liability["id"],
        "suggested_category": "",
        "needs_category_review": 0,
        "created_at": recent_payment["created_at"],
    }
    assert august["forecast"]["liability_paid"] == 205
    assert august["forecast"]["liability_remaining"] == 0
    assert august["forecast"]["repayment_outflow"] == 0
    assert august["recent"] == []
    assert august_liabilities["items"][0]["latest_payment_date"] == "2026-07-16"
    assert august_liabilities["items"][0]["latest_payment_amount"] == 205


def test_subscription_skip_and_liability_account_reuse_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    today = calendar_date.today().isoformat()
    subscription = client.post(
        "/api/subscriptions",
        json={
            "name": "云盘会员", "amount": 12, "cycle_months": 1,
            "next_charge_date": today, "category": "娱乐", "account": "微信",
        },
    ).json()["subscription"]
    listed = client.get(
        "/api/subscriptions", params={"month": today[:7], "include_inactive": True}
    )
    skipped = client.post(
        f"/api/subscriptions/{subscription['id']}/skip",
        json={"expected_date": today},
    )
    stale = client.post(
        f"/api/subscriptions/{subscription['id']}/skip",
        json={"expected_date": today},
    )
    assert listed.json()["items"][0]["charge_status"] == "due"
    assert skipped.status_code == 200
    assert stale.status_code == 400

    liability = client.post(
        "/api/liabilities",
        json={
            "name": "花呗", "provider": "支付宝", "kind": "consumer_credit",
            "statement_month": "2026-07", "due_amount": 300, "due_date": "2026-07-20",
            "credit_limit": 5000,
        },
    ).json()["liability"]
    august = client.patch(
        f"/api/liabilities/{liability['id']}",
        json={
            "statement_month": "2026-08", "due_amount": 420,
            "due_date": "2026-08-20", "minimum_payment": 0, "credit_limit": None,
        },
    )
    july_list = client.get("/api/liabilities", params={"month": "2026-07"}).json()
    august_list = client.get("/api/liabilities", params={"month": "2026-08"}).json()
    dashboard = client.get("/api/dashboard", params={"month": "2026-07"}).json()
    duplicate = client.post(
        "/api/liabilities",
        json={
            "name": "花呗", "statement_month": "2026-09",
            "due_amount": 500, "due_date": "2026-09-20",
        },
    )
    assert august.status_code == 200
    assert len(july_list["accounts"]) == 1
    assert july_list["accounts"][0]["credit_limit"] is None
    assert july_list["items"][0]["due_amount"] == 300
    assert august_list["items"][0]["due_amount"] == 420
    assert dashboard["forecast"]["current_debt"] == 720
    assert duplicate.status_code == 400


def test_undated_open_liability_is_displayed_as_carry_forward(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    liability = client.post(
        "/api/liabilities",
        json={
            "name": "欠朋友", "kind": "other", "statement_month": "2026-07",
            "due_amount": 600, "due_date": "",
        },
    ).json()["liability"]
    client.post(
        f"/api/liabilities/{liability['id']}/payments",
        json={"amount": 100, "paid_at": "2026-08-02", "statement_month": "2026-07"},
    )

    august = client.get("/api/liabilities", params={"month": "2026-08"}).json()
    dashboard = client.get("/api/dashboard", params={"month": "2026-08"}).json()

    assert august["items"][0]["statement_month"] == "2026-07"
    assert august["items"][0]["is_carried_forward"] is True
    assert august["items"][0]["remaining_amount"] == 500
    assert august["summary"]["remaining_amount"] == 0
    assert august["summary"]["carried_remaining_amount"] == 500
    assert dashboard["forecast"]["current_debt"] == 500
    assert dashboard["forecast"]["liability_remaining"] == 0


def test_inbox_and_chat_progress_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    created = client.post("/api/inbox", json={"text": "整理昨天的两张小票"})
    pending = client.get("/api/inbox")
    updated = client.patch(f"/api/inbox/{created.json()['item']['id']}", json={"status": "processing"})

    assert created.status_code == 200
    assert pending.json()["items"][0]["text"] == "整理昨天的两张小票"
    assert updated.json()["item"]["status"] == "processing"


def test_image_chat_uses_the_same_draft_confirmation_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    captured = {}

    def execute(conn, text, context, **kwargs):
        captured["images"] = kwargs["image_data_urls"]
        action = __import__("financial_agent").AgentAction(action="record", text=text)
        return action, {
            "drafts": [
                {
                    "date": "2026-07-09", "amount": 12, "direction": "expense",
                    "category": "餐饮", "account": "微信", "merchant": "早餐", "note": "",
                    "raw_text": text,
                }
            ]
        }

    monkeypatch.setattr("financial_agent.llm_uses_responses_api", lambda model: True)
    monkeypatch.setattr("web_app.llm_uses_responses_api", lambda model: True)
    monkeypatch.setattr("financial_agent.execute_agent_request", execute)
    response = client.post(
        "/api/chat/image",
        json={
            "text": "这是两笔消费截图",
            "images": ["data:image/png;base64,aGVsbG8="],
            "session_id": "image-test",
        },
    )
    history = client.get("/api/chat/history", params={"session_id": "image-test"}).json()
    attachment_meta = history["messages"][0]["attachments"][0]
    attachment = client.get(attachment_meta["url"])

    assert response.status_code == 200
    assert response.json()["kind"] == "drafts"
    assert captured["images"] == ("data:image/png;base64,aGVsbG8=",)
    assert history["messages"][0]["has_images"] is True
    assert attachment.status_code == 200
    assert attachment.headers["content-type"] == "image/png"
    assert attachment.content == b"hello"

    cleared = client.delete("/api/chat/history", params={"session_id": "image-test"})
    cleared_history = client.get("/api/chat/history", params={"session_id": "image-test"}).json()

    assert cleared.status_code == 200
    assert cleared.json()["attachments"] == 1
    assert cleared_history["messages"] == []


def test_dashboard_and_confirmed_transaction(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))

    empty = client.get("/api/dashboard", params={"month": "2026-07"})
    created = client.post(
        "/api/transactions/confirm",
        json={
            "date": "2026-07-09",
            "amount": 32,
            "direction": "expense",
            "category": "餐饮",
            "account": "微信",
            "merchant": "咖啡",
            "note": "",
            "raw_text": "今天咖啡 32 微信",
        },
    )
    dashboard = client.get("/api/dashboard", params={"month": "2026-07"})

    assert empty.status_code == 200
    assert created.status_code == 200
    assert created.json()["written"] is True
    assert dashboard.json()["summary"]["expense"] == 32
    assert dashboard.json()["forecast"]["cash_change"] == -32
    assert dashboard.json()["recent"][0]["merchant"] == "咖啡"


def test_capital_endpoint_calibrates_account_total(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))

    saved = client.put("/api/capital/2026-07", json={"current_balance": 3200})
    july = client.get("/api/dashboard", params={"month": "2026-07"})
    august = client.get("/api/dashboard", params={"month": "2026-08"})

    assert saved.status_code == 200
    assert july.json()["capital"]["current_balance"] == 3200
    assert august.json()["capital"]["current_balance"] == 3200
    assert august.json()["capital"]["account_model"] is True


def test_account_transfer_and_reconciliation_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "accounts.db"))
    today = calendar_date.today().isoformat()

    reconciled = client.post(
        "/api/accounts/reconcile",
        json={"account": "微信", "actual_balance": 100, "reconciled_on": today},
    )
    transferred = client.post(
        "/api/transfers",
        json={
            "source_account": "微信",
            "target_account": "支付宝",
            "amount": 30,
            "transferred_on": today,
        },
    )
    accounts = client.get("/api/accounts")
    records = client.get("/api/financial-records", params={"month": today[:7], "direction": "transfer"})

    assert reconciled.status_code == 200
    assert transferred.status_code == 200
    by_name = {item["name"]: item for item in accounts.json()["items"]}
    assert by_name["微信"]["balance"] == 70
    assert by_name["支付宝"]["balance"] == 30
    assert records.json()["results"][0]["record_type"] == "transfer"


def test_account_edit_and_delete_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "account-edit.db"))
    today = calendar_date.today().isoformat()

    created = client.post(
        "/api/accounts",
        json={"name": "测试银行卡", "kind": "bank", "actual_balance": 0, "reconciled_on": today},
    )
    edited = client.patch(
        "/api/accounts/%E6%B5%8B%E8%AF%95%E9%93%B6%E8%A1%8C%E5%8D%A1",
        json={"new_name": "测试钱包", "kind": "wallet"},
    )
    deleted = client.delete("/api/accounts/%E6%B5%8B%E8%AF%95%E9%92%B1%E5%8C%85")

    assert created.status_code == 200
    assert edited.status_code == 200
    assert edited.json()["account"]["kind"] == "wallet"
    assert deleted.status_code == 200


def test_chat_record_returns_draft_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    mock_native_tool(
        monkeypatch,
        "record_transactions",
        {
            "transactions": [
                {
                    "date": "2026-07-09",
                    "amount": 28,
                    "direction": "expense",
                    "category": "餐饮",
                    "account": "支付宝",
                    "merchant": "午饭",
                    "note": "",
                }
            ],
        },
    )

    response = client.post("/api/chat", json={"text": "午饭 28 支付宝", "session_id": "test"})
    dashboard = client.get("/api/dashboard", params={"month": "2026-07"})
    agent_logs = client.get("/api/logs/agent")

    assert response.status_code == 200
    assert response.json()["kind"] == "drafts"
    assert response.json()["draft_count"] == 1
    assert response.json()["requires_confirmation"] is True
    assert dashboard.json()["summary"]["expense"] == 0
    assert agent_logs.json()["logs"][0]["status"] == "success"
    assert agent_logs.json()["logs"][0]["action"] == "record"
    assert agent_logs.json()["logs"][0]["output_count"] == 1
    assert agent_logs.json()["logs"][0]["tool_mode"] == "native"
    assert agent_logs.json()["logs"][0]["steps"][0]["tool_name"] == "record_transactions"
    assert agent_logs.json()["logs"][0]["steps"][0]["risk"] == "write"
    assert "午饭 28 支付宝" not in str(agent_logs.json())


def test_chat_analyzes_multi_month_spending_with_read_only_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    for date, amount, merchant in (
        ("2026-05-08", 20, "面馆"),
        ("2026-07-08", 20, "面馆"),
        ("2026-07-09", 40, "咖啡店"),
    ):
        response = client.post(
            "/api/transactions/confirm",
            json={
                "date": date,
                "amount": amount,
                "direction": "expense",
                "category": "餐饮",
                "account": "微信",
                "merchant": merchant,
                "note": "",
                "raw_text": merchant,
            },
        )
        assert response.status_code == 200
    mock_native_tool(
        monkeypatch,
        "analyze_spending_trend",
        {"category": "餐饮", "end_month": "2026-07", "periods": 3},
        final_text="餐饮支出增长主要来自咖啡店新增消费。",
    )

    response = client.post(
        "/api/chat",
        json={"text": "分析最近三个月为什么餐饮变多", "session_id": "analysis-test"},
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "result"
    assert response.json()["analysis"]["comparison"]["change"] == 40
    assert "咖啡店" in response.json()["native_answer"]


def test_web_batch_drafts_confirm_and_undo_together(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    drafts = [
        {
            "date": "2026-07-09",
            "amount": 30,
            "direction": "expense",
            "category": "餐饮",
            "account": "微信",
            "merchant": "午饭",
            "note": "",
            "raw_text": "午饭30，地铁4，都是微信",
        },
        {
            "date": "2026-07-09",
            "amount": 4,
            "direction": "expense",
            "category": "交通",
            "account": "微信",
            "merchant": "地铁",
            "note": "",
            "raw_text": "午饭30，地铁4，都是微信",
        },
    ]
    model_drafts = [
        {key: value for key, value in draft.items() if key != "raw_text"}
        for draft in drafts
    ]
    mock_native_tool(
        monkeypatch,
        "record_transactions",
        {"transactions": model_drafts},
    )

    chat = client.post(
        "/api/chat",
        json={"text": "午饭30，地铁4，都是微信", "session_id": "batch-test"},
    )
    confirmed = client.post(
        "/api/transactions/confirm-batch",
        json={"transactions": chat.json()["drafts"]},
    )
    before_undo = client.get("/api/transactions", params={"month": "2026-07"})
    undone = client.post("/api/undo")
    after_undo = client.get("/api/transactions", params={"month": "2026-07"})
    agent_logs = client.get("/api/logs/agent", params={"limit": 1})

    assert chat.json()["draft_count"] == 2
    assert confirmed.json()["written"] == 2
    assert len(confirmed.json()["ids"]) == 2
    assert confirmed.json()["batch_id"]
    assert sum(item["amount"] for item in before_undo.json()["results"]) == 34
    assert len(undone.json()["ids"]) == 2
    assert after_undo.json()["results"] == []
    assert agent_logs.json()["logs"][0]["output_count"] == 2


def test_web_transaction_edit_delete_and_undo(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    created = client.post(
        "/api/transactions/confirm",
        json={
            "date": "2026-07-09",
            "amount": 32,
            "direction": "expense",
            "category": "餐饮",
            "account": "微信",
            "merchant": "咖啡",
            "note": "",
            "raw_text": "咖啡",
        },
    ).json()
    transaction_id = created["id"]

    updated = client.patch(
        f"/api/transactions/{transaction_id}", json={"amount": 28, "merchant": "拿铁"}
    )
    deleted = client.delete(f"/api/transactions/{transaction_id}")
    hidden = client.get("/api/transactions", params={"month": "2026-07"})
    undo = client.post("/api/undo")
    restored = client.get("/api/transactions", params={"month": "2026-07"})
    operation_logs = client.get("/api/logs/operations")

    assert updated.json()["updated"]["amount"] == 28
    assert deleted.status_code == 200
    assert hidden.json()["results"] == []
    assert undo.json()["undid"] == "transaction.delete"
    assert restored.json()["results"][0]["merchant"] == "拿铁"
    assert operation_logs.status_code == 200
    assert operation_logs.json()["logs"][0]["label"] == "撤销操作"
    update_log = next(
        item for item in operation_logs.json()["logs"] if item["action"] == "transaction.update"
    )
    assert update_log["source"] == "web"
    assert any(change["field"] == "金额" for change in update_log["changes"])
    assert "payload" not in update_log


def test_web_empty_payment_method_is_normalized_to_unspecified(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    created = client.post(
        "/api/transactions/confirm",
        json={
            "date": "2026-07-09",
            "amount": 5.5,
            "direction": "expense",
            "category": "购物",
            "account": "",
            "merchant": "买电池",
            "note": "",
            "raw_text": "买电池 5.5",
        },
    ).json()

    updated = client.patch(
        f"/api/transactions/{created['id']}",
        json={"account": ""},
    )

    assert created["transaction"]["account"] == "未指定"
    assert updated.json()["updated"]["account"] == "未指定"


def test_web_budget_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))

    saved = client.put("/api/budgets/餐饮", json={"month": "2026-07", "amount": 3000})
    listed = client.get("/api/budgets", params={"month": "2026-07"})
    deleted = client.delete("/api/budgets/餐饮", params={"month": "2026-07"})

    assert saved.status_code == 200
    assert listed.json()["budgets"][0]["amount"] == 3000
    assert deleted.json()["budget"]["deleted"] is True


def test_web_model_setting_persists_locally(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))

    changed = client.put(
        "/api/settings/model",
        json={"provider": "volcengine", "model": "glm-5-2-260617"},
    )
    health = client.get("/api/health")

    assert changed.status_code == 200
    assert health.json()["provider"] == "volcengine"
    assert health.json()["model"] == "glm-5-2-260617"
    assert "tool_mode" not in health.json()
    assert {item["id"] for item in health.json()["providers"]} == {"relay", "volcengine"}
    assert (tmp_path / "settings.json").exists()


def test_web_model_setting_rejects_invalid_name(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))

    response = client.put(
        "/api/settings/model",
        json={"provider": "relay", "model": "bad model name"},
    )

    assert response.status_code == 400


def test_web_model_setting_rejects_unknown_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))

    response = client.put(
        "/api/settings/model",
        json={"provider": "unknown", "model": "some-model"},
    )

    assert response.status_code == 400


def test_web_agent_error_is_logged_without_full_input(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_AGENT_DB", str(tmp_path / "web.db"))
    monkeypatch.setenv("ARK_API_KEY", "secret-test-key")
    monkeypatch.setattr(
        "financial_agent.llm_client",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: (_ for _ in ()).throw(
                        ValueError("模型输出格式错误 secret-test-key")
                    )
                )
            )
        ),
    )
    monkeypatch.setattr("financial_agent.llm_uses_responses_api", lambda model: False)

    response = client.post(
        "/api/chat",
        json={"text": "这是一段不应进入运行日志的完整输入", "session_id": "error-test"},
    )
    logs = client.get("/api/logs/agent", params={"status": "error"})

    assert response.status_code == 400
    assert logs.status_code == 200
    assert logs.json()["logs"][0]["status"] == "error"
    assert logs.json()["logs"][0]["error_type"] == "NativeToolCallingError"
    assert "这是一段不应进入运行日志的完整输入" not in str(logs.json())
    assert "secret-test-key" not in str(logs.json())
    assert "secret-test-key" not in str(response.json())
    assert "[REDACTED]" in logs.json()["logs"][0]["error_message"]
