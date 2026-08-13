from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4


PERSONAL_MEMORY_PREFERENCE_KEY = "personal_memory"
MAX_MEMORIES = 100


def _now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _text(value: Any, *, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def normalize_personal_memories(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    memories: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        memory_id = _text(raw.get("id"), maximum=80)
        title = _text(raw.get("title"), maximum=60)
        content = _text(raw.get("content"), maximum=500)
        if not memory_id or memory_id in seen or not title or not content:
            continue
        seen.add(memory_id)
        memories.append({
            "id": memory_id,
            "title": title,
            "content": content,
            "enabled": bool(raw.get("enabled", True)),
            "source": "agent" if raw.get("source") == "agent" else "manual",
            "created_at": _text(raw.get("created_at"), maximum=40),
            "updated_at": _text(raw.get("updated_at"), maximum=40),
        })
    return memories[:MAX_MEMORIES]


def list_personal_memories(value: Any) -> list[dict[str, Any]]:
    return sorted(
        normalize_personal_memories(value),
        key=lambda item: (item["enabled"] is False, item["updated_at"], item["created_at"]),
    )


def create_personal_memory(
    value: Any, *, title: str, content: str, source: str = "manual"
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    memories = normalize_personal_memories(value)
    if len(memories) >= MAX_MEMORIES:
        raise ValueError(f"个人偏好记忆最多保存 {MAX_MEMORIES} 条")
    normalized_title = _text(title, maximum=60)
    normalized_content = _text(content, maximum=500)
    if not normalized_title:
        raise ValueError("记忆标题不能为空")
    if not normalized_content:
        raise ValueError("记忆内容不能为空")
    now = _now()
    memory = {
        "id": f"memory_{uuid4().hex}", "title": normalized_title, "content": normalized_content,
        "enabled": True, "source": "agent" if source == "agent" else "manual",
        "created_at": now, "updated_at": now,
    }
    return [memory, *memories], memory


def update_personal_memory(
    value: Any, memory_id: str, *, title: str | None = None,
    content: str | None = None, enabled: bool | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    memories = normalize_personal_memories(value)
    for index, memory in enumerate(memories):
        if memory["id"] != memory_id:
            continue
        updated = dict(memory)
        if title is not None:
            updated["title"] = _text(title, maximum=60)
        if content is not None:
            updated["content"] = _text(content, maximum=500)
        if not updated["title"]:
            raise ValueError("记忆标题不能为空")
        if not updated["content"]:
            raise ValueError("记忆内容不能为空")
        if enabled is not None:
            updated["enabled"] = enabled
        updated["updated_at"] = _now()
        memories[index] = updated
        return memories, updated
    raise ValueError("找不到这条偏好记忆")


def delete_personal_memory(value: Any, memory_id: str) -> list[dict[str, Any]]:
    memories = normalize_personal_memories(value)
    remaining = [memory for memory in memories if memory["id"] != memory_id]
    if len(remaining) == len(memories):
        raise ValueError("找不到这条偏好记忆")
    return remaining
