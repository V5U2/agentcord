from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from pathlib import Path
import re
from typing import Any

from security import audit_log, memory_path, read_memory_json, write_memory_json

MEMORY_NAMESPACE = "users"
DEFAULT_TTL_DAYS = 30
DEFAULT_MAX_FACTS = 12
DEFAULT_MAX_BYTES = 4096
ALLOWED_FACT_TYPES = ("preferred_name", "likes", "dislikes", "timezone")
MAX_FACT_VALUE_LENGTH = 80


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


def extract_facts(text: str) -> list[MemoryFact]:
    lowered = text.strip()
    if not lowered:
        return []

    patterns: list[tuple[str, str]] = [
        (r"\bmy name is ([A-Za-z0-9 _-]{1,40}?)(?:\s+and\b|[.,!?]|$)", "preferred_name"),
        (r"\bcall me ([A-Za-z0-9 _-]{1,40}?)(?:\s+and\b|[.,!?]|$)", "preferred_name"),
        (r"\bi like ([A-Za-z0-9 ,_'/-]{1,60}?)(?:\s+and\b|[.,!?]|$)", "likes"),
        (r"\bi love ([A-Za-z0-9 ,_'/-]{1,60}?)(?:\s+and\b|[.,!?]|$)", "likes"),
        (r"\bi dislike ([A-Za-z0-9 ,_'/-]{1,60}?)(?:\s+and\b|[.,!?]|$)", "dislikes"),
        (r"\bi hate ([A-Za-z0-9 ,_'/-]{1,60}?)(?:\s+and\b|[.,!?]|$)", "dislikes"),
        (r"\bmy timezone is ([A-Za-z0-9/_+-]{1,40}?)(?:\s+and\b|[.,!?]|$)", "timezone"),
    ]

    facts: list[MemoryFact] = []
    for pattern, fact_type in patterns:
        for match in re.finditer(pattern, lowered, flags=re.IGNORECASE):
            value = " ".join(match.group(1).strip().split())
            if value:
                facts.append(MemoryFact(fact_type=fact_type, value=value))
    return facts[:4]


def normalize_facts(candidates: list[dict[str, Any] | MemoryFact]) -> list[MemoryFact]:
    facts: list[MemoryFact] = []
    seen = set()
    for candidate in candidates:
        if isinstance(candidate, MemoryFact):
            fact_type = candidate.fact_type
            value = candidate.value
        else:
            fact_type = str(candidate.get("type", "")).strip()
            value = " ".join(str(candidate.get("value", "")).strip().split())
        if fact_type not in ALLOWED_FACT_TYPES:
            continue
        if not value or len(value) > MAX_FACT_VALUE_LENGTH:
            continue
        key = (fact_type, value.lower())
        if key in seen:
            continue
        seen.add(key)
        facts.append(MemoryFact(fact_type=fact_type, value=value))
    return facts[:4]


def remember_text(user_id: int, guild_id: int | None, text: str, *, ttl_days: int = DEFAULT_TTL_DAYS, max_bytes: int = DEFAULT_MAX_BYTES) -> list[MemoryFact]:
    facts = normalize_facts(extract_facts(text))
    if not facts:
        return []
    return remember_facts(user_id, guild_id, facts, ttl_days=ttl_days, max_bytes=max_bytes)


def remember_facts(
    user_id: int,
    guild_id: int | None,
    facts: list[MemoryFact],
    *,
    ttl_days: int = DEFAULT_TTL_DAYS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[MemoryFact]:
    facts = normalize_facts(facts)
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
