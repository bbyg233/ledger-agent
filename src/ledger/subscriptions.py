from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date
from typing import Any
from uuid import uuid4

import financial_agent as core
from ledger.models import DuplicateTransactionError, TransactionDraft
from ledger.queries import month_range, normalize_month


SUBSCRIPTION_CYCLES = {1, 3, 6, 12}
LIABILITY_KINDS = {"credit_card", "consumer_credit", "installment", "other"}


def _add_months(value: date, months: int) -> date:
    target = value.month - 1 + months
    year = value.year + target // 12
    month = target % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _normalize_subscription_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("订阅名称不能为空")
    amount = round(float(payload.get("amount") or 0), 2)
    if amount <= 0:
        raise ValueError("订阅金额必须大于 0")
    cycle_months = int(payload.get("cycle_months") or 1)
    if cycle_months not in SUBSCRIPTION_CYCLES:
        raise ValueError("订阅周期只支持每月、每季、半年或每年")
    next_charge_date = date.fromisoformat(str(payload.get("next_charge_date") or "")).isoformat()
    category = core.canonical_reference(conn, "category", str(payload.get("category") or "其他"))
    account = core.ensure_asset_account(conn, str(payload.get("account") or "未指定"))
    return {
        "name": name,
        "amount": amount,
        "cycle_months": cycle_months,
        "next_charge_date": next_charge_date,
        "category": category,
        "account": account,
        "note": str(payload.get("note") or "").strip()[:300],
        "is_active": int(bool(payload.get("is_active", True))),
    }


def get_subscription(conn: sqlite3.Connection, subscription_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id[:80],)).fetchone()
    if row is None:
        raise ValueError("找不到订阅")
    return dict(row)


def create_subscription(
    conn: sqlite3.Connection, payload: dict[str, Any], actor: str = "web", *, commit: bool = True
) -> dict[str, Any]:
    item = _normalize_subscription_payload(conn, payload)
    duplicate = conn.execute(
        """
        SELECT id FROM subscriptions
        WHERE is_active = 1 AND lower(name) = lower(?) AND lower(account) = lower(?)
        LIMIT 1
        """,
        (item["name"], item["account"]),
    ).fetchone()
    if duplicate is not None:
        raise ValueError("已存在同名且支付方式相同的订阅，请编辑已有订阅")
    subscription_id = uuid4().hex
    now = core.now_iso()
    conn.execute(
        """
        INSERT INTO subscriptions
            (id, name, amount, cycle_months, next_charge_date, category, account, note, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (subscription_id, *item.values(), now, now),
    )
    core.audit(conn, "subscription.create", {"id": subscription_id, **item}, source=actor)
    if commit:
        conn.commit()
    return get_subscription(conn, subscription_id)


def update_subscription(
    conn: sqlite3.Connection,
    subscription_id: str,
    changes: dict[str, Any],
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    before = get_subscription(conn, subscription_id)
    allowed = {"name", "amount", "cycle_months", "next_charge_date", "category", "account", "note", "is_active"}
    merged = {**before, **{key: value for key, value in changes.items() if key in allowed and value is not None}}
    item = _normalize_subscription_payload(conn, merged)
    conn.execute(
        """
        UPDATE subscriptions
        SET name = ?, amount = ?, cycle_months = ?, next_charge_date = ?, category = ?, account = ?,
            note = ?, is_active = ?, updated_at = ?
        WHERE id = ?
        """,
        (*item.values(), core.now_iso(), subscription_id[:80]),
    )
    after = get_subscription(conn, subscription_id)
    core.audit(conn, "subscription.update", {"id": subscription_id[:80], "before": before, "after": after}, source=actor)
    if commit:
        conn.commit()
    return after


def list_subscriptions(conn: sqlite3.Connection, month: str = "", include_inactive: bool = False) -> dict[str, Any]:
    month = normalize_month(month)
    start, end = month_range(month)
    clauses = [] if include_inactive else ["is_active = 1"]
    sql = """
        SELECT s.*,
               (SELECT MAX(t.date) FROM transactions t
                WHERE t.source = 'subscription' AND t.source_id = s.id AND t.deleted_at IS NULL) AS last_charge_date,
               (SELECT COUNT(*) FROM transactions t
                WHERE t.source = 'subscription' AND t.source_id = s.id AND t.deleted_at IS NULL) AS charge_count
        FROM subscriptions s
    """
    if clauses:
        sql += f" WHERE {' AND '.join(f's.{clause}' for clause in clauses)}"
    sql += " ORDER BY s.is_active DESC, s.next_charge_date, s.name COLLATE NOCASE"
    items = [dict(row) for row in conn.execute(sql).fetchall()]
    today = date.today().isoformat()
    for item in items:
        item["charge_status"] = (
            "paused" if not item["is_active"] else
            "overdue" if item["next_charge_date"] < today else
            "due" if item["next_charge_date"] == today else "upcoming"
        )
    due_items = [
        item for item in items
        if item["is_active"] and start <= item["next_charge_date"] <= end
    ]
    overdue_items = [item for item in items if item["charge_status"] == "overdue"]
    return {
        "month": month,
        "items": items,
        "summary": {
            "due_count": len(due_items),
            "scheduled_amount": round(sum(float(item["amount"]) for item in due_items), 2),
            "active_count": sum(bool(item["is_active"]) for item in items),
            "overdue_count": len(overdue_items),
            "overdue_amount": round(sum(float(item["amount"]) for item in overdue_items), 2),
        },
    }


def record_subscription_charge(
    conn: sqlite3.Connection, subscription_id: str, actor: str = "web", *, commit: bool = True
) -> dict[str, Any]:
    subscription = get_subscription(conn, subscription_id)
    if not subscription["is_active"]:
        raise ValueError("已停用的订阅不能登记扣款")
    if subscription["next_charge_date"] > date.today().isoformat():
        raise ValueError("未来扣款尚未发生，不能确认已扣款；请先核对下次扣款日")
    draft = TransactionDraft(
        date=subscription["next_charge_date"],
        amount=float(subscription["amount"]),
        direction="expense",
        category=subscription["category"],
        account=subscription["account"],
        merchant=subscription["name"],
        note="订阅扣款" + (f"：{subscription['note']}" if subscription["note"] else ""),
        raw_text=f"订阅扣款：{subscription['name']}",
        source="subscription",
        source_id=subscription["id"],
        classification_source="manual",
        category_reason="订阅规则",
    )
    core.normalize_draft(conn, draft)
    duplicates = core.find_duplicate_transactions(conn, [draft])
    if duplicates:
        raise DuplicateTransactionError(duplicates)
    try:
        transaction_id = core.insert_transaction(conn, draft, actor=actor)
        next_charge_date = _add_months(date.fromisoformat(subscription["next_charge_date"]), int(subscription["cycle_months"])).isoformat()
        conn.execute(
            "UPDATE subscriptions SET next_charge_date = ?, updated_at = ? WHERE id = ?",
            (next_charge_date, core.now_iso(), subscription_id[:80]),
        )
        core.audit(
            conn,
            "subscription.charge",
            {
                "id": subscription_id[:80],
                "transaction_id": transaction_id,
                "charged_date": subscription["next_charge_date"],
                "previous_next_charge_date": subscription["next_charge_date"],
                "next_charge_date": next_charge_date,
            },
            source=actor,
        )
        core.remember_merchant_category(conn, draft.merchant, draft.category)
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return {"transaction_id": transaction_id, "subscription": get_subscription(conn, subscription_id)}


def reverse_subscription_charge(
    conn: sqlite3.Connection,
    transaction_id: int,
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    transaction = core.transaction_snapshot(core.get_transaction(conn, transaction_id))
    if transaction.get("source") != "subscription" or not transaction.get("source_id"):
        raise ValueError("这不是订阅生成的扣款记录")
    subscription_id = str(transaction["source_id"])
    subscription = get_subscription(conn, subscription_id)
    later_charge = conn.execute(
        """
        SELECT id
        FROM transactions
        WHERE source = 'subscription' AND source_id = ?
          AND deleted_at IS NULL AND date > ? AND id <> ?
        LIMIT 1
        """,
        (subscription_id, transaction["date"], transaction_id),
    ).fetchone()
    if later_charge is not None:
        raise ValueError("只能撤销这个订阅最近一次确认的扣款")

    charge_payload: dict[str, Any] = {}
    for row in conn.execute(
        "SELECT payload FROM audit_log WHERE action = 'subscription.charge' ORDER BY id DESC"
    ).fetchall():
        try:
            candidate = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        if int(candidate.get("transaction_id") or 0) == transaction_id:
            charge_payload = candidate
            break
    previous_next_charge_date = str(
        charge_payload.get("previous_next_charge_date")
        or charge_payload.get("charged_date")
        or transaction["date"]
    )
    expected_next_charge_date = str(
        charge_payload.get("next_charge_date")
        or _add_months(
            date.fromisoformat(previous_next_charge_date),
            int(subscription["cycle_months"]),
        ).isoformat()
    )
    if subscription["next_charge_date"] != expected_next_charge_date:
        raise ValueError("订阅计划在扣款后已修改，不能自动回退日期")

    try:
        core.soft_delete_without_audit(conn, transaction_id)
        conn.execute(
            "UPDATE subscriptions SET next_charge_date = ?, updated_at = ? WHERE id = ?",
            (previous_next_charge_date, core.now_iso(), subscription_id),
        )
        after = get_subscription(conn, subscription_id)
        core.audit(
            conn,
            "subscription.charge.reverse",
            {
                "id": subscription_id,
                "transaction_id": transaction_id,
                "before": subscription,
                "after": after,
                "deleted_transaction": transaction,
            },
            source=actor,
        )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return {"transaction_id": transaction_id, "subscription": after}


def skip_subscription_charge(
    conn: sqlite3.Connection,
    subscription_id: str,
    expected_date: str = "",
    actor: str = "web",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    subscription = get_subscription(conn, subscription_id)
    if not subscription["is_active"]:
        raise ValueError("已停用的订阅不能跳过扣款")
    if expected_date and subscription["next_charge_date"] != expected_date:
        raise ValueError("订阅扣款日已经变化，请刷新后重试")
    skipped_date = subscription["next_charge_date"]
    next_charge_date = _add_months(
        date.fromisoformat(skipped_date), int(subscription["cycle_months"])
    ).isoformat()
    conn.execute(
        "UPDATE subscriptions SET next_charge_date = ?, updated_at = ? WHERE id = ?",
        (next_charge_date, core.now_iso(), subscription_id[:80]),
    )
    core.audit(
        conn,
        "subscription.skip",
        {
            "id": subscription_id[:80],
            "skipped_date": skipped_date,
            "next_charge_date": next_charge_date,
        },
        source=actor,
    )
    if commit:
        conn.commit()
    return {
        "skipped_date": skipped_date,
        "subscription": get_subscription(conn, subscription_id),
    }
