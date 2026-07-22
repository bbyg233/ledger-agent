from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date
from typing import Any


def month_range(month: str) -> tuple[str, str]:
    year, month_num = map(int, month.split("-"))
    last_day = calendar.monthrange(year, month_num)[1]
    return f"{year:04d}-{month_num:02d}-01", f"{year:04d}-{month_num:02d}-{last_day:02d}"


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def normalize_month(month: str) -> str:
    return month if month else current_month()


def rows_for_month(conn: sqlite3.Connection, month: str) -> list[sqlite3.Row]:
    start, end = month_range(month)
    return list(
        conn.execute(
            "SELECT * FROM transactions WHERE date BETWEEN ? AND ? AND deleted_at IS NULL ORDER BY date, id",
            (start, end),
        )
    )


def credit_charges_for_month(conn: sqlite3.Connection, month: str) -> list[sqlite3.Row]:
    start, end = month_range(month)
    return list(
        conn.execute(
            """
            SELECT c.*, l.name AS liability_name
            FROM liability_charges c
            JOIN liabilities l ON l.id = c.liability_id
            WHERE c.charged_at BETWEEN ? AND ?
            ORDER BY c.charged_at, c.id
            """,
            (start, end),
        )
    )


def search_transactions(
    conn: sqlite3.Connection,
    query: str = "",
    month: str = "",
    category: str = "",
    account: str = "",
    direction: str = "",
    min_amount: float | None = None,
    max_amount: float | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    clauses = ["deleted_at IS NULL"]
    params: list[Any] = []
    if month:
        start, end = month_range(month)
        clauses.append("date BETWEEN ? AND ?")
        params.extend([start, end])
    if category:
        clauses.append("category = ?")
        params.append(category)
    if account:
        clauses.append("account = ?")
        params.append(account)
    if direction:
        if direction not in {"income", "expense"}:
            raise ValueError("direction 必须是 income 或 expense")
        clauses.append("direction = ?")
        params.append(direction)
    if min_amount is not None:
        clauses.append("amount >= ?")
        params.append(min_amount)
    if max_amount is not None:
        clauses.append("amount <= ?")
        params.append(max_amount)
    if query:
        like = f"%{query}%"
        clauses.append(
            "(merchant LIKE ? OR note LIKE ? OR raw_text LIKE ? OR category LIKE ? OR account LIKE ?)"
        )
        params.extend([like, like, like, like, like])

    safe_limit = min(max(int(limit), 1), 200)
    sql = f"""
        SELECT id, date, amount, direction, category, account, merchant, note, raw_text,
               source, source_id, category_confidence, category_reason,
               classification_source, suggested_category, proposed_category,
               needs_category_review, created_at
        FROM transactions
        WHERE {' AND '.join(clauses)}
        ORDER BY date DESC, id DESC
        LIMIT ?
    """
    params.append(safe_limit)
    return [dict(row) for row in conn.execute(sql, params)]


def recent_financial_records(
    conn: sqlite3.Connection,
    query: str = "",
    month: str = "",
    category: str = "",
    account: str = "",
    direction: str = "",
    min_amount: float | None = None,
    max_amount: float | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return transactions, repayments and account transfers by actual cash-flow date."""
    transaction_clauses = ["deleted_at IS NULL"]
    payment_clauses = ["1 = 1"]
    transfer_clauses = ["1 = 1"]
    charge_clauses = ["1 = 1"]
    transaction_params: list[Any] = []
    payment_params: list[Any] = []
    transfer_params: list[Any] = []
    charge_params: list[Any] = []
    if month:
        start, end = month_range(normalize_month(month))
        transaction_clauses.append("date BETWEEN ? AND ?")
        payment_clauses.append("p.paid_at BETWEEN ? AND ?")
        transfer_clauses.append("transferred_on BETWEEN ? AND ?")
        charge_clauses.append("c.charged_at BETWEEN ? AND ?")
        transaction_params.extend([start, end])
        payment_params.extend([start, end])
        transfer_params.extend([start, end])
        charge_params.extend([start, end])
    if direction:
        if direction not in {"income", "expense", "repayment", "transfer", "liability"}:
            raise ValueError("direction 必须是 income、expense、repayment、transfer 或 liability")
        if direction == "repayment":
            transaction_clauses.append("0 = 1")
            transfer_clauses.append("0 = 1")
            charge_clauses.append("0 = 1")
        elif direction == "transfer":
            transaction_clauses.append("0 = 1")
            payment_clauses.append("0 = 1")
            charge_clauses.append("0 = 1")
        elif direction == "liability":
            transaction_clauses.append("0 = 1")
            payment_clauses.append("0 = 1")
            transfer_clauses.append("0 = 1")
            charge_clauses.append("0 = 1")
        else:
            transaction_clauses.append("direction = ?")
            transaction_params.append(direction)
            payment_clauses.append("0 = 1")
            transfer_clauses.append("0 = 1")
            if direction != "expense":
                charge_clauses.append("0 = 1")
    if category:
        if category == "还款":
            transaction_clauses.append("0 = 1")
            transfer_clauses.append("0 = 1")
            charge_clauses.append("0 = 1")
        else:
            transaction_clauses.append("category = ?")
            transaction_params.append(category)
            payment_clauses.append("0 = 1")
            transfer_clauses.append("0 = 1")
            charge_clauses.append("c.category = ?")
            charge_params.append(category)
    if account:
        transaction_clauses.append("account = ?")
        payment_clauses.append(
            "COALESCE(NULLIF(p.account, ''), l.repayment_account) = ?"
        )
        transfer_clauses.append("(source_account = ? OR target_account = ?)")
        charge_clauses.append("l.name = ?")
        transaction_params.append(account)
        payment_params.append(account)
        transfer_params.extend([account, account])
        charge_params.append(account)
    if min_amount is not None:
        transaction_clauses.append("amount >= ?")
        payment_clauses.append("p.amount >= ?")
        transfer_clauses.append("amount >= ?")
        transaction_params.append(min_amount)
        payment_params.append(min_amount)
        transfer_params.append(min_amount)
        charge_clauses.append("c.amount >= ?")
        charge_params.append(min_amount)
    if max_amount is not None:
        transaction_clauses.append("amount <= ?")
        payment_clauses.append("p.amount <= ?")
        transfer_clauses.append("amount <= ?")
        transaction_params.append(max_amount)
        payment_params.append(max_amount)
        transfer_params.append(max_amount)
        charge_clauses.append("c.amount <= ?")
        charge_params.append(max_amount)
    if query:
        like = f"%{query}%"
        transaction_clauses.append(
            "(merchant LIKE ? OR note LIKE ? OR raw_text LIKE ? OR category LIKE ? OR account LIKE ?)"
        )
        transaction_params.extend([like, like, like, like, like])
        payment_clauses.append(
            "(l.name LIKE ? OR p.note LIKE ? OR p.account LIKE ? OR p.statement_month LIKE ?)"
        )
        transfer_clauses.append("(source_account LIKE ? OR target_account LIKE ? OR note LIKE ?)")
        payment_params.extend([like, like, like, like])
        transfer_params.extend([like, like, like])
        charge_clauses.append(
            "(l.name LIKE ? OR c.merchant LIKE ? OR c.note LIKE ? OR c.category LIKE ? OR c.statement_month LIKE ?)"
        )
        charge_params.extend([like, like, like, like, like])
    safe_limit = min(max(int(limit), 1), 200)
    sql = f"""
        SELECT id, date, amount, direction, category, account, merchant, note,
               statement_month, record_type, liability_id,
               source, source_id, suggested_category, needs_category_review, created_at
        FROM (
            SELECT id, date, amount, direction, category, account, merchant, note,
                   '' AS statement_month, 'transaction' AS record_type,
                   '' AS liability_id, source, source_id,
                   suggested_category, needs_category_review, created_at
            FROM transactions
            WHERE {' AND '.join(transaction_clauses)}

            UNION ALL

            SELECT p.id, p.paid_at AS date, p.amount, 'repayment' AS direction,
                   '还款' AS category,
                   COALESCE(NULLIF(p.account, ''), NULLIF(l.repayment_account, ''), '未指定') AS account,
                   l.name AS merchant, p.note, p.statement_month,
                   'liability_payment' AS record_type, p.liability_id,
                   'liability_payment' AS source, p.liability_id AS source_id,
                   '' AS suggested_category, 0 AS needs_category_review, p.created_at
            FROM liability_payments p
            JOIN liabilities l ON l.id = p.liability_id
            WHERE {' AND '.join(payment_clauses)}

            UNION ALL

            SELECT id, transferred_on AS date, amount, 'transfer' AS direction,
                   '转账' AS category, source_account || ' -> ' || target_account AS account,
                   target_account AS merchant, note, '' AS statement_month,
                   'transfer' AS record_type, '' AS liability_id,
                   'transfer' AS source, '' AS source_id,
                   '' AS suggested_category, 0 AS needs_category_review, created_at
            FROM transfers
            WHERE {' AND '.join(transfer_clauses)}

            UNION ALL

            SELECT c.id, c.charged_at AS date, c.amount, 'expense' AS direction,
                   c.category, l.name AS account, c.merchant, c.note, c.statement_month,
                   'liability_charge' AS record_type, c.liability_id,
                   'liability_charge' AS source, c.liability_id AS source_id,
                   '' AS suggested_category, 0 AS needs_category_review, c.created_at
            FROM liability_charges c
            JOIN liabilities l ON l.id = c.liability_id
            WHERE {' AND '.join(charge_clauses)}
        )
        ORDER BY date DESC, created_at DESC, id DESC
        LIMIT ?
    """
    params = [*transaction_params, *payment_params, *transfer_params, *charge_params, safe_limit]
    records = [dict(row) for row in conn.execute(sql, params)]
    if not direction or direction == "liability":
        records.extend(
            liability_change_records(
                conn, query=query, month=month, category=category, account=account
            )
        )
    records.sort(key=lambda item: (item["date"], item["created_at"], str(item["id"])), reverse=True)
    return records[:safe_limit]


def liability_change_records(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    month: str = "",
    category: str = "",
    account: str = "",
) -> list[dict[str, Any]]:
    """Expose confirmed debt-statement changes beside cash records without treating them as cash flow."""
    if category and category != "待还变动":
        return []
    if account and account != "不影响现金":
        return []
    start, end = month_range(normalize_month(month)) if month else ("", "")
    rows = conn.execute(
        """
        SELECT id, action, payload, source, created_at
        FROM audit_log
        WHERE action IN ('liability.create', 'liability.update')
        ORDER BY id DESC
        LIMIT 200
        """
    ).fetchall()
    results: list[dict[str, Any]] = []
    query_key = query.casefold().strip()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        item = payload.get("after") if isinstance(payload.get("after"), dict) else payload
        name = str(item.get("name") or "")
        statement_month = str(item.get("statement_month") or "")
        due_amount = item.get("due_amount")
        if not name or due_amount is None:
            continue
        event_date = str(row["created_at"])[:10]
        if month and not (start <= event_date <= end):
            continue
        detail = f"归属 {statement_month} 账单" if statement_month else "待还账户更新"
        searchable = " ".join([name, detail, "待还变动", str(item.get("provider") or "")]).casefold()
        if query_key and query_key not in searchable:
            continue
        results.append(
            {
                "id": f"liability-change-{row['id']}",
                "date": event_date,
                "amount": round(float(due_amount), 2),
                "direction": "liability",
                "category": "待还变动",
                "account": "不影响现金",
                "merchant": name,
                "note": detail,
                "statement_month": statement_month,
                "record_type": "liability_statement",
                "event_kind": "recovery" if row["source"] == "recovery" else "change",
                "liability_id": str(item.get("id") or payload.get("id") or ""),
                "source": str(row["action"]),
                "source_id": str(row["id"]),
                "suggested_category": "",
                "needs_category_review": 0,
                "created_at": str(row["created_at"]),
            }
        )
    return results


def where_money_went(
    conn: sqlite3.Connection,
    month: str = "",
    query: str = "",
    group_by: str = "category",
    limit: int = 10,
) -> dict[str, Any]:
    if group_by not in {"category", "merchant", "account"}:
        raise ValueError("group_by 只能是 category、merchant 或 account")
    clauses = ["deleted_at IS NULL", "direction = 'expense'"]
    params: list[Any] = []
    if month:
        start, end = month_range(month)
        clauses.append("date BETWEEN ? AND ?")
        params.extend([start, end])
    if query:
        like = f"%{query}%"
        clauses.append(
            "(merchant LIKE ? OR note LIKE ? OR raw_text LIKE ? OR category LIKE ? OR account LIKE ?)"
        )
        params.extend([like, like, like, like, like])

    safe_limit = min(max(int(limit), 1), 100)
    transaction_rows = [dict(row) for row in conn.execute(
        f"""
        SELECT {group_by} AS name, COUNT(*) AS count, ROUND(SUM(amount), 2) AS total
        FROM transactions WHERE {' AND '.join(clauses)} GROUP BY {group_by}
        """,
        params,
    )]
    charge_clauses = ["1 = 1"]
    charge_params: list[Any] = []
    if month:
        start, end = month_range(month)
        charge_clauses.append("c.charged_at BETWEEN ? AND ?")
        charge_params.extend([start, end])
    if query:
        like = f"%{query}%"
        charge_clauses.append(
            "(c.merchant LIKE ? OR c.note LIKE ? OR c.category LIKE ? OR l.name LIKE ?)"
        )
        charge_params.extend([like, like, like, like])
    charge_field = {"category": "c.category", "merchant": "c.merchant", "account": "l.name"}[group_by]
    charge_rows = [dict(row) for row in conn.execute(
        f"""
        SELECT {charge_field} AS name, COUNT(*) AS count, ROUND(SUM(c.amount), 2) AS total
        FROM liability_charges c JOIN liabilities l ON l.id = c.liability_id
        WHERE {' AND '.join(charge_clauses)} GROUP BY {charge_field}
        """,
        charge_params,
    )]
    grouped: dict[str, dict[str, Any]] = {}
    for row in [*transaction_rows, *charge_rows]:
        name = str(row["name"] or "未指定")
        item = grouped.setdefault(name, {"name": name, "count": 0, "total": 0.0})
        item["count"] += int(row["count"])
        item["total"] = round(float(item["total"]) + float(row["total"]), 2)
    rows = sorted(grouped.values(), key=lambda item: item["total"], reverse=True)[:safe_limit]
    total = round(sum(float(row["total"]) for row in grouped.values()), 2)
    for row in rows:
        row["share"] = round(row["total"] / total, 4) if total else 0
    return {
        "month": month or "all",
        "query": query,
        "group_by": group_by,
        "total_expense": round(total, 2),
        "items": rows,
    }


def summarize(conn: sqlite3.Connection, month: str) -> dict[str, Any]:
    rows = rows_for_month(conn, month)
    credit_charges = credit_charges_for_month(conn, month)
    income = sum(row["amount"] for row in rows if row["direction"] == "income")
    cash_expense = sum(row["amount"] for row in rows if row["direction"] == "expense")
    credit_expense = sum(row["amount"] for row in credit_charges)
    expense = cash_expense + credit_expense
    by_category: dict[str, float] = {}
    for row in rows:
        if row["direction"] == "expense":
            by_category[row["category"]] = round(
                by_category.get(row["category"], 0) + row["amount"], 2
            )
    for row in credit_charges:
        by_category[row["category"]] = round(
            by_category.get(row["category"], 0) + row["amount"], 2
        )
    budgets = {
        row["category"]: row["amount"]
        for row in conn.execute("SELECT category, amount FROM budgets WHERE month = ?", (month,))
    }
    budget_status = {
        category: {
            "budget": budget,
            "spent": by_category.get(category, 0),
            "remaining": round(budget - by_category.get(category, 0), 2),
        }
        for category, budget in budgets.items()
    }
    return {
        "month": month,
        "income": round(income, 2),
        "expense": round(expense, 2),
        "net": round(income - expense, 2),
        "cash_expense": round(cash_expense, 2),
        "credit_expense": round(credit_expense, 2),
        "count": len(rows) + len(credit_charges),
        "by_category": dict(sorted(by_category.items(), key=lambda item: item[1], reverse=True)),
        "budget_status": budget_status,
    }
