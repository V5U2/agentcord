from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from security import audit_log, memory_path, read_memory_json, write_memory_json

MEMORY_NAMESPACE = "users"
DEFAULT_TTL_DAYS = 30
DEFAULT_MAX_FACTS = 24
DEFAULT_MAX_BYTES = 8192
DEFAULT_ALLOWED_FACT_TYPES = (
    "preferred_name",
    "nickname",
    "pronouns",
    "likes",
    "dislikes",
    "timezone",
    "favorite_team",
    "favorite_game",
    "location",
    "work_context",
    "personal_context",
    "preference",
    "interests",
    "hobbies",
    "goals",
    "projects",
    "role",
    "expertise",
    "communication_style",
)
MAX_FACT_VALUE_LENGTH = 160


@dataclass(frozen=True)
class MemoryFact:
    fact_type: str
    value: str


def _scope_id(guild_id: int | None) -> str:
    return str(guild_id) if guild_id is not None else "dm"


def _memory_file(user_id: int, guild_id: int | None) -> Path:
    return memory_path(MEMORY_NAMESPACE, _scope_id(guild_id), f"{user_id}.json")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_store(user_id: int, guild_id: int | None) -> dict[str, Any]:
    path = _memory_file(user_id, guild_id)
    if not path.exists():
        return {"facts": []}
    try:
        return read_memory_json(path)
    except (JSONDecodeError, FileNotFoundError):
        return {"facts": []}


def _save_store(user_id: int, guild_id: int | None, store: dict[str, Any], max_bytes: int) -> None:
    payload = {"facts": store["facts"][:DEFAULT_MAX_FACTS]}
    encoded = str(payload).encode("utf-8")
    if len(encoded) > max_bytes:
        payload["facts"] = payload["facts"][: max(1, DEFAULT_MAX_FACTS // 2)]
    write_memory_json(_memory_file(user_id, guild_id), payload)


def normalize_facts(
    candidates: list[dict[str, Any] | MemoryFact],
    *,
    allowed_fact_types: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_FACT_TYPES,
) -> list[MemoryFact]:
    facts: list[MemoryFact] = []
    seen = set()
    allowed = tuple(allowed_fact_types)
    for candidate in candidates:
        if isinstance(candidate, MemoryFact):
            fact_type = candidate.fact_type
            value = candidate.value
        else:
            fact_type = str(candidate.get("type", "")).strip()
            value = " ".join(str(candidate.get("value", "")).strip().split())
        if fact_type not in allowed:
            continue
        if not value or len(value) > MAX_FACT_VALUE_LENGTH:
            continue
        key = (fact_type, value.lower())
        if key in seen:
            continue
        seen.add(key)
        facts.append(MemoryFact(fact_type=fact_type, value=value))
    return facts[:8]


def render_memory_grounding_context(target_user_id: int, recent_messages: list[dict[str, Any]], latest_message: str) -> str:
    lines = [f"Target user ID: {target_user_id}", "Recent conversation context:"]
    for item in recent_messages:
        author_id = item.get("author_id", "unknown")
        content = " ".join(str(item.get("content", "")).split())
        if not content:
            continue
        lines.append(f"- {author_id}: {content[:240]}")
    lines.append("Latest message from target user:")
    lines.append(latest_message[:500])
    return "\n".join(lines)


def remember_facts(
    user_id: int,
    guild_id: int | None,
    facts: list[MemoryFact],
    *,
    ttl_days: int = DEFAULT_TTL_DAYS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allowed_fact_types: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_FACT_TYPES,
) -> list[MemoryFact]:
    facts = normalize_facts(facts, allowed_fact_types=allowed_fact_types)
    if not facts:
        return []

    store = _load_store(user_id, guild_id)
    now = _now_iso()
    expires_at = (datetime.now(UTC) + timedelta(days=ttl_days)).isoformat()
    existing = [fact for fact in store.get("facts", []) if datetime.fromisoformat(fact["expires_at"]) > datetime.now(UTC)]

    for new_fact in facts:
        existing = [fact for fact in existing if not (fact["type"] == new_fact.fact_type and fact["value"].lower() == new_fact.value.lower())]
        existing.append({"type": new_fact.fact_type, "value": new_fact.value, "updated_at": now, "expires_at": expires_at})

    store["facts"] = existing[-DEFAULT_MAX_FACTS:]
    _save_store(user_id, guild_id, store, max_bytes)
    audit_log("memory_write", guild_id=_scope_id(guild_id), user_id=user_id, fact_count=len(facts))
    return facts


def _active_facts(user_id: int, guild_id: int | None) -> list[dict[str, Any]]:
    store = _load_store(user_id, guild_id)
    now = datetime.now(UTC)
    facts = [fact for fact in store.get("facts", []) if datetime.fromisoformat(fact["expires_at"]) > now]
    if facts != store.get("facts", []):
        store["facts"] = facts
        _save_store(user_id, guild_id, store, DEFAULT_MAX_BYTES)
    return facts


def render_memory_context(user_id: int, guild_id: int | None) -> str:
    facts = _active_facts(user_id, guild_id)
    if not facts:
        return ""
    lines = [f"- {fact['type']}: {fact['value']}" for fact in facts[:DEFAULT_MAX_FACTS]]
    return "Known user memory (typed facts only):\n" + "\n".join(lines)


def list_memories(user_id: int, guild_id: int | None) -> list[str]:
    return [f"{fact['type']}: {fact['value']}" for fact in _active_facts(user_id, guild_id)]


def forget_memories(user_id: int, guild_id: int | None, needle: str | None = None) -> int:
    store = _load_store(user_id, guild_id)
    facts = store.get("facts", [])
    if needle:
        lowered = needle.lower()
        kept = [fact for fact in facts if lowered not in fact["value"].lower() and lowered not in fact["type"].lower()]
    else:
        kept = []
    removed = len(facts) - len(kept)
    store["facts"] = kept
    _save_store(user_id, guild_id, store, DEFAULT_MAX_BYTES)
    audit_log("memory_forget", guild_id=_scope_id(guild_id), user_id=user_id, removed=removed)
    return removed
