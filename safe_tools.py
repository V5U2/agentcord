from __future__ import annotations

from html import unescape
from json import loads
import re
from typing import Any
from urllib.parse import urlencode

from security import audit_log, validate_outbound_url


def enabled_tools(config: dict[str, Any]) -> list[dict[str, Any]]:
    features = config.get("features", {})
    if not features.get("tools", False):
        return []

    tool_config = config.get("tools", {})
    tools: list[dict[str, Any]] = []
    if tool_config.get("web_search", {}).get("enabled", False):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web using a safe allowlisted backend and return short text results.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "Search query"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            }
        )
    if tool_config.get("web_fetch", {}).get("enabled", False):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": "Fetch and sanitize a webpage from an allowlisted host.",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string", "description": "Absolute URL to fetch"}},
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                },
            }
        )
    return tools


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(text))).strip()


async def execute_tool_call(tool_name: str, arguments_json: str, config: dict[str, Any], httpx_client: Any) -> str:
    if not config.get("features", {}).get("tools", False):
        raise RuntimeError("Tool execution is disabled")

    tool_config = config.get("tools", {}).get(tool_name)
    if not tool_config or not tool_config.get("enabled", False):
        raise RuntimeError(f"Tool '{tool_name}' is disabled")

    try:
        arguments = loads(arguments_json or "{}")
    except ValueError as exc:
        raise RuntimeError(f"Invalid tool arguments for {tool_name}: {exc}") from exc

    if tool_name == "web_search":
        return await _web_search(arguments, tool_config, httpx_client)
    if tool_name == "web_fetch":
        return await _web_fetch(arguments, tool_config, httpx_client)
    raise RuntimeError(f"Unknown tool '{tool_name}'")


async def _web_search(arguments: dict[str, Any], tool_config: dict[str, Any], httpx_client: Any) -> str:
    query = str(arguments.get("query", "")).strip()
    if not query:
        raise RuntimeError("web_search requires a non-empty query")

    max_results = int(tool_config.get("max_results", 5))
    max_chars = int(tool_config.get("max_response_chars", 2000))
    timeout = float(tool_config.get("timeout_seconds", 10))
    query_string = urlencode({"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"})
    search_url = validate_outbound_url(
        f"https://api.duckduckgo.com/?{query_string}",
        tool_config.get("allowed_hosts", ["api.duckduckgo.com"]),
    )

    response = await httpx_client.get(search_url, timeout=timeout, follow_redirects=False)
    response.raise_for_status()
    payload = response.json()

    results: list[str] = []
    if abstract := payload.get("AbstractText"):
        results.append(f"Answer: {abstract}")

    for topic in payload.get("RelatedTopics", []):
        if isinstance(topic, dict):
            if text := topic.get("Text"):
                results.append(text)
            for nested in topic.get("Topics", []):
                if isinstance(nested, dict) and nested.get("Text"):
                    results.append(nested["Text"])
        if len(results) >= max_results:
            break

    if not results:
        results.append("No concise results were returned by the safe search backend.")

    content = "\n".join(f"- {item}" for item in results[:max_results])
    audit_log("tool_web_search", query=query, result_count=min(len(results), max_results))
    return content[:max_chars]


async def _web_fetch(arguments: dict[str, Any], tool_config: dict[str, Any], httpx_client: Any) -> str:
    url = str(arguments.get("url", "")).strip()
    if not url:
        raise RuntimeError("web_fetch requires a non-empty url")

    timeout = float(tool_config.get("timeout_seconds", 10))
    max_chars = int(tool_config.get("max_response_chars", 4000))
    validated_url = validate_outbound_url(url, tool_config.get("allowed_hosts", []))
    response = await httpx_client.get(validated_url, timeout=timeout, follow_redirects=False)
    response.raise_for_status()
    sanitized = _strip_html(response.text)
    audit_log("tool_web_fetch", url=validated_url, chars=min(len(sanitized), max_chars))
    return sanitized[:max_chars]
