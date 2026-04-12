from __future__ import annotations

from json import JSONDecodeError
from pathlib import Path
from typing import Any

from security import audit_log, memory_path, read_memory_json, write_memory_json

CLANKER_NAMESPACE = "clankers"


def _clanker_file(channel_id: int) -> Path:
    return memory_path(CLANKER_NAMESPACE, f"{channel_id}.json")


def _load_clankers(channel_id: int) -> dict[str, Any]:
    path = _clanker_file(channel_id)
    if not path.exists():
        return {"bots": {}}
    try:
        return read_memory_json(path)
    except (JSONDecodeError, FileNotFoundError):
        return {"bots": {}}


def _save_clankers(channel_id: int, store: dict[str, Any]) -> None:
    write_memory_json(_clanker_file(channel_id), {"bots": store.get("bots", {})})


def add_clanker(channel_id: int, bot_id: int, label: str) -> None:
    store = _load_clankers(channel_id)
    store.setdefault("bots", {})[str(bot_id)] = {"label": label}
    _save_clankers(channel_id, store)
    audit_log("clanker_add", channel_id=channel_id, bot_id=bot_id, label=label)


def remove_clanker(channel_id: int, bot_id: int) -> bool:
    store = _load_clankers(channel_id)
    removed = store.setdefault("bots", {}).pop(str(bot_id), None) is not None
    _save_clankers(channel_id, store)
    audit_log("clanker_remove", channel_id=channel_id, bot_id=bot_id, removed=removed)
    return removed


def is_clanker(channel_id: int, bot_id: int) -> bool:
    return str(bot_id) in _load_clankers(channel_id).get("bots", {})


def list_clankers(channel_id: int) -> list[tuple[int, str]]:
    bots = _load_clankers(channel_id).get("bots", {})
    return [(int(bot_id), str(record.get("label") or bot_id)) for bot_id, record in sorted(bots.items(), key=lambda item: item[1].get("label", item[0]))]


def list_clanker_channels() -> list[int]:
    root = memory_path(CLANKER_NAMESPACE)
    return sorted(int(path.stem) for path in root.glob("*.json") if path.stem.isdigit())
