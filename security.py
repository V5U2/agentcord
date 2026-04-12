from __future__ import annotations

from json import JSONDecodeError, dumps, loads
import ipaddress
import logging
from pathlib import Path
import re
import socket
from typing import Any
from urllib.parse import urlparse

MEMORY_ROOT = Path("data/memory")
SYSTEM_PROMPT_OVERRIDE_PATH = MEMORY_ROOT / "system" / "system_prompt.txt"
DEFAULT_CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"
CODEX_AUTH_FILE_API_KEY_MODE = "codex_auth_file_api_key"
CODEX_CHATGPT_TOKEN_MODE = "codex_chatgpt_token"
REDACTED = "***REDACTED***"


def is_wake_name_match(content: str, wake_names: list[str]) -> bool:
    return any(re.search(rf"(^|\W){re.escape(name)}($|\W)", content, flags=re.IGNORECASE) for name in wake_names if name.strip())


def _memory_root() -> Path:
    return MEMORY_ROOT.resolve()


def ensure_memory_root() -> Path:
    root = _memory_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def memory_path(*parts: str) -> Path:
    root = ensure_memory_root()
    candidate = root.joinpath(*parts).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Refusing to access path outside memory root")
    return candidate


def write_memory_text(path: Path, content: str) -> None:
    safe_path = _validate_memory_path(path, "write")
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(content, encoding="utf-8")


def read_memory_text(path: Path) -> str:
    return _validate_memory_path(path, "read").read_text(encoding="utf-8")


def _validate_memory_path(path: Path, action: str) -> Path:
    root = ensure_memory_root()
    safe_path = path.resolve()
    if safe_path != root and root not in safe_path.parents:
        raise ValueError(f"Refusing to {action} outside memory root")
    return safe_path


def write_memory_json(path: Path, data: Any) -> None:
    write_memory_text(path, dumps(data, ensure_ascii=True, indent=2, sort_keys=True))


def read_memory_json(path: Path) -> Any:
    return loads(read_memory_text(path))


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if any(word in key.lower() for word in ("key", "token", "secret")) else redact_value(v)
            for key, v in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        if value.startswith("sk-") or "token" in value.lower():
            return REDACTED
    return value


def audit_log(event: str, **fields: Any) -> None:
    safe_fields = redact_value(fields)
    logging.info("AUDIT %s %s", event, dumps(safe_fields, sort_keys=True))


def load_system_prompt(base_prompt: str) -> str:
    if SYSTEM_PROMPT_OVERRIDE_PATH.exists():
        return read_memory_text(SYSTEM_PROMPT_OVERRIDE_PATH)
    return base_prompt


def save_system_prompt_override(prompt: str) -> None:
    write_memory_text(SYSTEM_PROMPT_OVERRIDE_PATH, prompt)


def clear_system_prompt_override() -> None:
    if SYSTEM_PROMPT_OVERRIDE_PATH.exists():
        _validate_memory_path(SYSTEM_PROMPT_OVERRIDE_PATH, "delete").unlink()


def resolve_provider_api_key(provider_name: str, provider_config: dict[str, Any]) -> str:
    if auth_mode := provider_config.get("auth_mode"):
        if auth_mode == CODEX_AUTH_FILE_API_KEY_MODE:
            auth_path = Path(provider_config.get("codex_auth_file", DEFAULT_CODEX_AUTH_FILE)).expanduser()
            try:
                auth_blob = loads(auth_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, JSONDecodeError) as exc:
                raise RuntimeError(f"Unable to load Codex auth file for provider '{provider_name}': {exc}") from exc

            api_key = auth_blob.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError(f"Codex auth file does not contain an OPENAI_API_KEY for provider '{provider_name}'")
            return api_key

        if auth_mode == CODEX_CHATGPT_TOKEN_MODE:
            auth_path = Path(provider_config.get("codex_auth_file", DEFAULT_CODEX_AUTH_FILE)).expanduser()
            try:
                auth_blob = loads(auth_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, JSONDecodeError) as exc:
                raise RuntimeError(f"Unable to load Codex auth file for provider '{provider_name}': {exc}") from exc

            access_token = (auth_blob.get("tokens") or {}).get("access_token")
            if not access_token:
                raise RuntimeError(f"Codex auth file does not contain a ChatGPT access token for provider '{provider_name}'")
            return access_token

        raise RuntimeError(f"Unsupported auth_mode '{auth_mode}' for provider '{provider_name}'")

    if env_name := provider_config.get("api_key_env"):
        from os import environ

        api_key = environ.get(env_name)
        if not api_key:
            raise RuntimeError(f"Environment variable '{env_name}' is not set for provider '{provider_name}'")
        return api_key

    if api_key_file := provider_config.get("api_key_file"):
        file_path = Path(api_key_file).expanduser()
        try:
            return file_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise RuntimeError(f"API key file '{file_path}' was not found for provider '{provider_name}'") from exc

    return provider_config.get("api_key", "sk-no-key-required")


def _host_is_allowed(host: str, allowed_hosts: list[str]) -> bool:
    if not allowed_hosts:
        return False
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


def _host_resolves_to_private_address(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise RuntimeError(f"Failed to resolve host '{host}': {exc}") from exc

    for family, _, _, _, sockaddr in infos:
        if family == socket.AF_INET:
            ip_text = sockaddr[0]
        elif family == socket.AF_INET6:
            ip_text = sockaddr[0]
        else:
            continue

        ip_addr = ipaddress.ip_address(ip_text)
        if ip_addr.is_private or ip_addr.is_loopback or ip_addr.is_link_local or ip_addr.is_multicast or ip_addr.is_reserved:
            return True
    return False


def validate_outbound_url(url: str, allowed_hosts: list[str]) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("Only http and https URLs are allowed")
    if not parsed.hostname:
        raise RuntimeError("URL must include a hostname")
    if not _host_is_allowed(parsed.hostname, allowed_hosts):
        raise RuntimeError(f"Host '{parsed.hostname}' is not allowlisted")
    if _host_resolves_to_private_address(parsed.hostname):
        raise RuntimeError(f"Host '{parsed.hostname}' resolves to a private or otherwise blocked address")
    return url
