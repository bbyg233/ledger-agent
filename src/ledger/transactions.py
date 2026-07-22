from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import financial_agent as core
from ledger.models import DuplicateTransactionError, TransactionDraft


def backfill_transaction_hashes(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT * FROM transactions WHERE entry_hash = '' OR entry_hash IS NULL"
    ).fetchall()
    for row in rows:
        draft = TransactionDraft(
            date=row["date"],
            amount=float(row["amount"]),
            direction=row["direction"],
            category=row["category"],
            account=row["account"],
            merchant=row["merchant"],
            note=row["note"],
            raw_text=row["raw_text"],
        )
        conn.execute(
            "UPDATE transactions SET entry_hash = ? WHERE id = ?",
            (transaction_entry_hash(draft), row["id"]),
        )


def decode_bill_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别账单编码，请导出为 UTF-8 或 GB18030 CSV")


def bill_csv_rows(path: Path) -> list[dict[str, str]]:
    lines = decode_bill_file(path).splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "交易时间" in line or ("交易创建时间" in line and ("," in line or "\t" in line))
        ),
        None,
    )
    if header_index is None:
        raise ValueError("没有找到支付宝或微信账单表头")
    sample = "\n".join(lines[header_index : header_index + 5])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])), dialect=dialect)
    rows: list[dict[str, str]] = []
    for row in reader:
        clean = {
            str(key).strip().lstrip("\ufeff"): str(value or "").strip()
            for key, value in row.items()
            if key is not None
        }
        if any(clean.values()):
            rows.append(clean)
    return rows


def first_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        if row.get(name):
            return row[name].strip()
    return ""


def parse_import_amount(value: str) -> float:
    cleaned = re.sub(r"[^\d.\-]", "", value.replace(",", ""))
    if not cleaned:
        raise ValueError("金额为空")
    return round(abs(float(cleaned)), 2)


def import_row_to_draft(row: dict[str, str], source: str) -> tuple[TransactionDraft | None, bool]:
    flow = first_value(row, "收/支", "收支", "资金流向")
    if "不计收支" in flow:
        return None, False
    direction = "income" if ("收入" in flow or flow == "收") else "expense"
    timestamp = first_value(row, "交易时间", "交易创建时间", "付款时间")
    match = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", timestamp)
    if not match:
        raise ValueError("交易日期为空或格式不支持")
    year, month_num, day = map(int, re.findall(r"\d+", match.group(0)))
    transaction_date = date(year, month_num, day).isoformat()
    amount = parse_import_amount(first_value(row, "金额(元)", "金额（元）", "金额"))
    merchant = first_value(row, "交易对方", "商户名称", "商品名称", "商品")
    product = first_value(row, "商品名称", "商品")
    note = first_value(row, "备注")
    category = "待分类"
    source_id = first_value(row, "交易单号", "交易号", "支付宝交易号", "微信支付单号")
    account = first_value(row, "支付方式") or ("支付宝" if source == "alipay" else "微信")
    raw_text = json.dumps(row, ensure_ascii=False, sort_keys=True)
    fingerprint = source_id or "|".join(
        [transaction_date, f"{amount:.2f}", direction, merchant, product, account]
    )
    import_hash = hashlib.sha256(f"{source}|{fingerprint}".encode()).hexdigest()
    draft = TransactionDraft(
        date=transaction_date,
        amount=amount,
        direction=direction,
        category=category,
        account=account[:40] or "未指定",
        merchant=(merchant or product)[:80],
        note=note[:200],
        raw_text=raw_text,
        source=source,
        source_id=source_id[:100],
        import_hash=import_hash,
    )
    return draft, True


def preview_bill_import(conn: sqlite3.Connection, path: Path, source: str) -> dict[str, Any]:
    if source not in {"alipay", "wechat"}:
        raise ValueError("source 只能是 alipay 或 wechat")
    if not path.is_file():
        raise ValueError(f"账单文件不存在: {path}")
    drafts: list[TransactionDraft] = []
    skipped: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    duplicate_count = 0
    low_confidence_count = 0
    for index, row in enumerate(bill_csv_rows(path), start=1):
        try:
            draft, low_confidence = import_row_to_draft(row, source)
        except (TypeError, ValueError) as exc:
            skipped.append({"row": index, "reason": str(exc)})
            continue
        if draft is None:
            skipped.append({"row": index, "reason": "不计收支"})
            continue
        exists = conn.execute(
            "SELECT 1 FROM transactions WHERE import_hash = ? LIMIT 1", (draft.import_hash,)
        ).fetchone()
        if exists or draft.import_hash in seen_hashes:
            duplicate_count += 1
            continue
        seen_hashes.add(draft.import_hash)
        drafts.append(draft)
        low_confidence_count += int(low_confidence)
    return {
        "source": source,
        "file": str(path),
        "drafts": drafts,
        "ready": len(drafts),
        "duplicates": duplicate_count,
        "low_confidence": low_confidence_count,
        "skipped": skipped,
    }


def import_bill(conn: sqlite3.Connection, path: Path, source: str, dry_run: bool, assume_yes: bool) -> dict[str, Any]:
    preview = preview_bill_import(conn, path, source)
    public_preview = {**preview, "drafts": [asdict(draft) for draft in preview["drafts"]]}
    if dry_run:
        return {**public_preview, "written": 0}
    if not assume_yes:
        raise ValueError("导入会批量写入账本；请先用 --dry-run 检查，再加 --yes 确认")
    if preview["drafts"]:
        batch = add_transactions(conn, preview["drafts"], allow_duplicate=True)
        ids = batch["ids"]
        batch_id = batch["batch_id"]
    else:
        ids = []
        batch_id = ""
    core.audit(
        conn,
        "import.complete",
        {
            "source": source,
            "file": path.name,
            "written": len(ids),
            "ids": ids,
            "batch_id": batch_id,
        },
    )
    conn.commit()
    return {
        **public_preview,
        "drafts": [],
        "written": len(ids),
        "ids": ids,
        "batch_id": batch_id,
    }


def validate_transaction_payload(payload: dict[str, Any], raw_text: str) -> TransactionDraft:
    required = ["date", "amount", "direction", "category", "account", "merchant", "note"]
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError(f"LLM 返回缺少字段: {', '.join(missing)}")

    parsed_date = date.fromisoformat(str(payload["date"])).isoformat()
    amount = round(float(payload["amount"]), 2)
    if amount <= 0:
        raise ValueError("账单金额必须大于 0")

    direction = str(payload["direction"])
    if direction not in {"income", "expense"}:
        raise ValueError("direction 必须是 income 或 expense")

    confidence = max(0.0, min(float(payload.get("category_confidence", 1.0)), 1.0))
    return TransactionDraft(
        date=parsed_date,
        amount=amount,
        direction=direction,
        category=str(payload["category"])[:40] or ("其他收入" if direction == "income" else "其他"),
        account=str(payload["account"])[:40] or "未指定",
        merchant=str(payload["merchant"])[:80],
        note=str(payload["note"])[:200],
        raw_text=raw_text,
        category_confidence=confidence,
        category_reason=str(payload.get("category_reason") or "")[:200],
        classification_source=str(payload.get("classification_source") or "llm")[:30],
        suggested_category=str(payload.get("suggested_category") or "")[:40],
        proposed_category=str(payload.get("proposed_category") or "")[:40],
        needs_category_review=bool(payload.get("needs_category_review", False)),
    )


def refresh_transaction_hashes(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT * FROM transactions").fetchall():
        draft = TransactionDraft(
            date=row["date"], amount=float(row["amount"]), direction=row["direction"],
            category=row["category"], account=row["account"], merchant=row["merchant"],
            note=row["note"], raw_text=row["raw_text"],
        )
        conn.execute(
            "UPDATE transactions SET entry_hash = ? WHERE id = ?",
            (transaction_entry_hash(draft), row["id"]),
        )


def normalize_draft(conn: sqlite3.Connection, draft: TransactionDraft) -> TransactionDraft:
    user_confirmed = draft.classification_source in {"manual", "user_confirmed"}
    rule = None if user_confirmed else core.merchant_category_rule(conn, draft.merchant)
    if rule is not None:
        draft.category = core.canonical_reference(conn, "category", rule["category"])
        draft.category_confidence = 1.0
        draft.category_reason = f"采用已确认的商户分类（确认 {rule['confirmations']} 次）"
        draft.classification_source = "merchant_rule"
        draft.proposed_category = ""
        draft.needs_category_review = False
    else:
        draft.category = core.canonical_reference(conn, "category", draft.category)
        standard_names = {item["name"] for item in core.list_reference_values(conn, "category")}
        if draft.proposed_category:
            canonical_proposal = core.canonical_reference(conn, "category", draft.proposed_category)
            if canonical_proposal in standard_names and canonical_proposal != "待分类":
                draft.category = canonical_proposal
                draft.suggested_category = canonical_proposal
                draft.proposed_category = ""
            else:
                draft.proposed_category = core.clean_reference_name(draft.proposed_category)
        if draft.category not in standard_names:
            draft.proposed_category = draft.proposed_category or draft.category
            draft.suggested_category = ""
            draft.category = "待分类"
            draft.category_confidence = 0.0
            draft.needs_category_review = True
        elif draft.category == "待分类" and draft.proposed_category:
            draft.needs_category_review = True
        elif draft.category_confidence < classification_threshold():
            draft.needs_category_review = True
    draft.account = core.ensure_asset_account(conn, draft.account)
    draft.entry_hash = transaction_entry_hash(draft)
    return draft


def classification_threshold() -> float:
    return max(0.0, min(float(core.env_float("LEDGER_AGENT_CLASSIFICATION_THRESHOLD", 0.85)), 1.0))


def transaction_entry_hash(draft: TransactionDraft) -> str:
    values = [
        draft.date,
        f"{draft.amount:.2f}",
        draft.direction,
        draft.category.strip().casefold(),
        draft.account.strip().casefold(),
        draft.merchant.strip().casefold(),
    ]
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def find_duplicate_transactions(
    conn: sqlite3.Connection, drafts: list[TransactionDraft]
) -> list[dict[str, Any]]:
    duplicates: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, draft in enumerate(drafts):
        normalize_draft(conn, draft)
        if draft.entry_hash in seen:
            duplicates.append(
                {"draft_index": index, "duplicate_of_draft_index": seen[draft.entry_hash], "match": "batch"}
            )
            continue
        seen[draft.entry_hash] = index
        row = conn.execute(
            """
            SELECT id, date, amount, direction, category, account, merchant
            FROM transactions
            WHERE entry_hash = ? AND deleted_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (draft.entry_hash,),
        ).fetchone()
        if row is not None:
            duplicates.append({"draft_index": index, "match": "ledger", "transaction": dict(row)})
    return duplicates


def insert_transaction(
    conn: sqlite3.Connection,
    draft: TransactionDraft,
    actor: str,
    batch_id: str = "",
) -> int:
    now = core.now_iso()
    cursor = conn.execute(
        """
        INSERT INTO transactions
            (date, amount, direction, category, account, merchant, note, raw_text,
             source, source_id, import_hash, entry_hash, category_confidence,
             category_reason, classification_source, suggested_category,
             proposed_category, needs_category_review, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft.date,
            draft.amount,
            draft.direction,
            draft.category,
            draft.account,
            draft.merchant,
            draft.note,
            draft.raw_text,
            draft.source,
            draft.source_id,
            draft.import_hash,
            draft.entry_hash,
            draft.category_confidence,
            draft.category_reason,
            draft.classification_source,
            draft.suggested_category,
            draft.proposed_category,
            int(draft.needs_category_review),
            now,
        ),
    )
    payload = {"id": int(cursor.lastrowid), **asdict(draft)}
    if batch_id:
        payload["batch_id"] = batch_id
    core.audit(conn, "transaction.create", payload, source=actor)
    return int(cursor.lastrowid)


def add_transaction(
    conn: sqlite3.Connection,
    draft: TransactionDraft,
    actor: str = "local",
    allow_duplicate: bool = False,
) -> int:
    normalize_draft(conn, draft)
    duplicates = find_duplicate_transactions(conn, [draft])
    if duplicates and not allow_duplicate:
        raise DuplicateTransactionError(duplicates)
    transaction_id = insert_transaction(conn, draft, actor=actor)
    if draft.classification_source in {"manual", "user_confirmed"}:
        core.remember_merchant_category(conn, draft.merchant, draft.category)
    conn.commit()
    return transaction_id


def add_transactions(
    conn: sqlite3.Connection,
    drafts: list[TransactionDraft],
    actor: str = "local",
    allow_duplicate: bool = False,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    if not drafts:
        raise ValueError("至少需要一条账单草稿")
    if len(drafts) > 20:
        raise ValueError("单次最多写入 20 条账单")
    duplicates = find_duplicate_transactions(conn, drafts)
    if duplicates and not allow_duplicate:
        raise DuplicateTransactionError(duplicates)
    batch_id = uuid4().hex
    try:
        ids = [
            insert_transaction(conn, draft, actor=actor, batch_id=batch_id)
            for draft in drafts
        ]
        for draft in drafts:
            if draft.classification_source in {"manual", "user_confirmed"}:
                core.remember_merchant_category(conn, draft.merchant, draft.category)
        core.audit(
            conn,
            "transaction.batch_create",
            {"batch_id": batch_id, "ids": ids, "count": len(ids)},
            source=actor,
        )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return {"ids": ids, "batch_id": batch_id, "written": len(ids)}


def transaction_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    fields = [
        "id",
        "date",
        "amount",
        "direction",
        "category",
        "account",
        "merchant",
        "note",
        "raw_text",
        "source",
        "source_id",
        "import_hash",
        "entry_hash",
        "category_confidence",
        "category_reason",
        "classification_source",
        "suggested_category",
        "proposed_category",
        "needs_category_review",
        "created_at",
        "deleted_at",
    ]
    return {field: row[field] for field in fields if field in row.keys()}


def get_transaction(conn: sqlite3.Connection, transaction_id: int, include_deleted: bool = False) -> sqlite3.Row:
    sql = "SELECT * FROM transactions WHERE id = ?"
    params: tuple[Any, ...] = (transaction_id,)
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise ValueError(f"找不到账单: {transaction_id}")
    return row


def update_transaction(
    conn: sqlite3.Connection,
    transaction_id: int,
    changes: dict[str, Any],
    actor: str = "local",
) -> dict[str, Any]:
    allowed = {"date", "amount", "direction", "category", "account", "merchant", "note"}
    clean_changes = {key: value for key, value in changes.items() if key in allowed and value is not None}
    if not clean_changes:
        raise ValueError("没有可更新的字段")
    before = transaction_snapshot(get_transaction(conn, transaction_id))
    if before.get("source") == "subscription":
        raise ValueError("订阅扣款不能作为普通账单修改；请先撤销订阅扣款")
    if "date" in clean_changes:
        clean_changes["date"] = date.fromisoformat(str(clean_changes["date"])).isoformat()
    if "amount" in clean_changes:
        amount = round(float(clean_changes["amount"]), 2)
        if amount <= 0:
            raise ValueError("账单金额必须大于 0")
        clean_changes["amount"] = amount
    if "direction" in clean_changes and clean_changes["direction"] not in {"income", "expense"}:
        raise ValueError("direction 必须是 income 或 expense")
    if "account" in clean_changes:
        clean_changes["account"] = core.ensure_asset_account(conn, clean_changes["account"])
    if "category" in clean_changes:
        clean_changes["category"] = core.canonical_reference(conn, "category", clean_changes["category"])
        if clean_changes["category"] != before["category"]:
            clean_changes["category_confidence"] = 1.0
            clean_changes["category_reason"] = "用户手动确认"
            clean_changes["classification_source"] = "manual"
            clean_changes["suggested_category"] = ""
            clean_changes["proposed_category"] = ""
            clean_changes["needs_category_review"] = 0

    fingerprint_data = {**before, **clean_changes}
    fingerprint_draft = TransactionDraft(
        date=fingerprint_data["date"],
        amount=float(fingerprint_data["amount"]),
        direction=fingerprint_data["direction"],
        category=fingerprint_data["category"],
        account=fingerprint_data["account"],
        merchant=fingerprint_data["merchant"],
        note=fingerprint_data["note"],
        raw_text=fingerprint_data["raw_text"],
    )
    clean_changes["entry_hash"] = transaction_entry_hash(fingerprint_draft)
    assignments = ", ".join(f"{field} = ?" for field in clean_changes)
    conn.execute(
        f"UPDATE transactions SET {assignments} WHERE id = ? AND deleted_at IS NULL",
        (*clean_changes.values(), transaction_id),
    )
    after = transaction_snapshot(get_transaction(conn, transaction_id))
    if "category" in changes and after.get("category") != before.get("category"):
        core.remember_merchant_category(conn, str(after.get("merchant") or ""), after["category"])
    core.audit(
        conn,
        "transaction.update",
        {"id": transaction_id, "before": before, "after": after},
        source=actor,
    )
    conn.commit()
    return after


def soft_delete_transaction(
    conn: sqlite3.Connection,
    transaction_id: int,
    actor: str = "local",
) -> dict[str, Any]:
    before = transaction_snapshot(get_transaction(conn, transaction_id))
    if before.get("source") == "subscription":
        raise ValueError("订阅扣款不能作为普通账单删除；请使用撤销订阅扣款")
    deleted_at = datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE transactions SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (deleted_at, transaction_id))
    after = transaction_snapshot(get_transaction(conn, transaction_id, include_deleted=True))
    core.audit(
        conn,
        "transaction.delete",
        {"id": transaction_id, "before": before, "after": after},
        source=actor,
    )
    conn.commit()
    return after


def undo_last_transaction_action(
    conn: sqlite3.Connection,
    actor: str = "local",
) -> dict[str, Any]:
    undone_ids: set[int] = set()
    for undo_row in conn.execute(
        "SELECT payload FROM audit_log WHERE action = 'transaction.undo'"
    ):
        undo_payload = json.loads(undo_row["payload"])
        if undo_payload.get("undid_audit_id") is not None:
            undone_ids.add(int(undo_payload["undid_audit_id"]))
        undone_ids.update(int(value) for value in undo_payload.get("undid_audit_ids", []))
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC").fetchall()
    row = next(
        (
            candidate
            for candidate in rows
            if candidate["action"] in core.REVERSIBLE_ACTIONS
            and candidate["id"] not in undone_ids
            and not (
                candidate["action"] == "transaction.create"
                and json.loads(candidate["payload"]).get("source") == "subscription"
            )
        ),
        None,
    )
    if row is None:
        raise ValueError("没有可撤销的账单操作")

    payload = json.loads(row["payload"])
    action = row["action"]
    if action == "transaction.create":
        batch_id = str(payload.get("batch_id") or "")
        if batch_id:
            batch_rows: list[tuple[sqlite3.Row, dict[str, Any]]] = []
            for candidate in rows:
                if candidate["action"] != "transaction.create" or candidate["id"] in undone_ids:
                    continue
                candidate_payload = json.loads(candidate["payload"])
                if candidate_payload.get("batch_id") == batch_id:
                    batch_rows.append((candidate, candidate_payload))
            transaction_ids = [int(item[1]["id"]) for item in batch_rows]
            for transaction_id in transaction_ids:
                soft_delete_without_audit(conn, transaction_id)
            for merchant in {str(item[1].get("merchant") or "") for item in batch_rows}:
                core.rebuild_merchant_category_rule(conn, merchant)
            core.audit(
                conn,
                "transaction.undo",
                {
                    "undid_audit_ids": [item[0]["id"] for item in batch_rows],
                    "undid_action": action,
                    "ids": transaction_ids,
                    "batch_id": batch_id,
                },
                source=actor,
            )
            conn.commit()
            return {"undid": action, "ids": transaction_ids, "batch_id": batch_id}
        transaction_id = int(payload["id"]) if "id" in payload else None
        if transaction_id is None:
            latest = conn.execute("SELECT id FROM transactions WHERE raw_text = ? ORDER BY id DESC LIMIT 1", (payload["raw_text"],)).fetchone()
            if latest is None:
                raise ValueError("无法定位要撤销的新建账单")
            transaction_id = int(latest["id"])
        soft_delete_without_audit(conn, transaction_id)
        core.rebuild_merchant_category_rule(conn, str(payload.get("merchant") or ""))
        core.audit(
            conn,
            "transaction.undo",
            {"undid_audit_id": row["id"], "undid_action": action, "id": transaction_id},
            source=actor,
        )
        conn.commit()
        return {"undid": action, "id": transaction_id}

    before = payload["before"]
    transaction_id = int(payload["id"])
    restore_transaction_snapshot(conn, before)
    if action == "transaction.update" and payload.get("after", {}).get("category") != before.get("category"):
        core.rebuild_merchant_category_rule(
            conn, str(payload.get("after", {}).get("merchant") or before.get("merchant") or "")
        )
    core.audit(
        conn,
        "transaction.undo",
        {"undid_audit_id": row["id"], "undid_action": action, "id": transaction_id},
        source=actor,
    )
    conn.commit()
    return {"undid": action, "id": transaction_id}


def preview_last_transaction_action(conn: sqlite3.Connection) -> dict[str, Any]:
    undone_ids: set[int] = set()
    for undo_row in conn.execute(
        "SELECT payload FROM audit_log WHERE action = 'transaction.undo'"
    ).fetchall():
        payload = json.loads(undo_row["payload"])
        if payload.get("undid_audit_id") is not None:
            undone_ids.add(int(payload["undid_audit_id"]))
        undone_ids.update(int(value) for value in payload.get("undid_audit_ids", []))
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC").fetchall()
    row = next(
        (
            candidate for candidate in rows
            if candidate["action"] in core.REVERSIBLE_ACTIONS and candidate["id"] not in undone_ids
            and not (
                candidate["action"] == "transaction.create"
                and json.loads(candidate["payload"]).get("source") == "subscription"
            )
        ),
        None,
    )
    if row is None:
        raise ValueError("没有可撤销的账单操作")
    payload = json.loads(row["payload"])
    batch_id = str(payload.get("batch_id") or "")
    if row["action"] == "transaction.create" and batch_id:
        count = sum(
            1
            for candidate in rows
            if candidate["action"] == "transaction.create"
            and candidate["id"] not in undone_ids
            and json.loads(candidate["payload"]).get("batch_id") == batch_id
        )
        return {
            "action": row["action"],
            "count": count,
            "batch_id": batch_id,
            "message": f"将整批撤销 {count} 笔账单，撤销后这些账单会从统计中移除。",
        }
    return {
        "action": row["action"],
        "count": 1,
        "batch_id": "",
        "message": f"将撤销最近一次{core.AUDIT_LABELS.get(row['action'], '账单')}操作。",
    }


def soft_delete_without_audit(conn: sqlite3.Connection, transaction_id: int) -> None:
    deleted_at = datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE transactions SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (deleted_at, transaction_id))


def restore_transaction_snapshot(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
    fields = [
        "date", "amount", "direction", "category", "account", "merchant", "note",
        "raw_text", "entry_hash", "created_at", "deleted_at",
        "category_confidence", "category_reason", "classification_source",
        "suggested_category", "proposed_category", "needs_category_review",
    ]
    assignments = ", ".join(f"{field} = ?" for field in fields)
    conn.execute(
        f"UPDATE transactions SET {assignments} WHERE id = ?",
        (*(snapshot.get(field) for field in fields), snapshot["id"]),
    )
