from __future__ import annotations

from html import unescape
from email.utils import parsedate_to_datetime
from json import loads
from os import environ
import re
from typing import Any
from urllib.parse import urlencode
from defusedxml import ElementTree as ET

from security import audit_log, validate_outbound_url


def enabled_tools(config: dict[str, Any], provider_name: str | None = None) -> list[dict[str, Any]]:
    features = config.get("features", {})
    if not features.get("tools", False):
        return []

    tool_config = config.get("tools", {})
    tools: list[dict[str, Any]] = []
    web_search_config = tool_config.get("web_search", {})
    if web_search_config.get("enabled", False) and web_search_config.get("backend") == "openrouter_server" and provider_name == "openrouter":
        parameters = {
            key: value
            for key, value in {
                "engine": web_search_config.get("engine", "auto"),
                "max_results": web_search_config.get("max_results", 5),
                "max_total_results": web_search_config.get("max_total_results"),
                "search_context_size": web_search_config.get("search_context_size"),
                "allowed_domains": web_search_config.get("allowed_domains"),
                "excluded_domains": web_search_config.get("excluded_domains"),
                "user_location": web_search_config.get("user_location"),
            }.items()
            if value is not None
        }
        tools.append({"type": "openrouter:web_search", "parameters": parameters})
    elif web_search_config.get("enabled", False):
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
    if tool_config.get("rss_feed", {}).get("enabled", False):
        configured_feeds = sorted((tool_config.get("rss_feed", {}).get("feeds") or {}).keys())
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "rss_feed",
                    "description": f"Read recent items from a configured RSS/Atom feed by friendly name. Available feeds: {', '.join(configured_feeds) or 'none'}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "feed": {"type": "string", "description": "Friendly feed name from the configured feed list"},
                            "limit": {"type": "integer", "description": "Maximum number of feed items to return"},
                        },
                        "required": ["feed"],
                        "additionalProperties": False,
                    },
                },
            }
        )
    return tools


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(text))).strip()


async def execute_tool_call(tool_name: str, arguments_json: str, config: dict[str, Any], httpx_client: Any) -> str:
    if tool_name.startswith("openrouter:"):
        raise RuntimeError(f"Server-side tool '{tool_name}' must be executed by OpenRouter, not the local broker")

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
    if tool_name == "rss_feed":
        return await _rss_feed(arguments, tool_config, httpx_client)
    raise RuntimeError(f"Unknown tool '{tool_name}'")


async def _web_search(arguments: dict[str, Any], tool_config: dict[str, Any], httpx_client: Any) -> str:
    query = str(arguments.get("query", "")).strip()
    if not query:
        raise RuntimeError("web_search requires a non-empty query")

    max_results = int(tool_config.get("max_results", 5))
    max_chars = int(tool_config.get("max_response_chars", 2000))
    timeout = float(tool_config.get("timeout_seconds", 10))
    backend = tool_config.get("backend", "firecrawl")

    if backend == "firecrawl":
        results = await _firecrawl_search(query, tool_config, httpx_client, max_results, timeout)
    elif backend == "duckduckgo_instant_answer":
        results = await _duckduckgo_instant_answer_search(query, tool_config, httpx_client, max_results, timeout)
    else:
        raise RuntimeError(f"Unsupported web_search backend '{backend}'")

    if not results:
        audit_log("tool_web_search_empty", query=query, backend=backend)
        return f"The safe search backend '{backend}' returned no usable results for this query. Tell the user the search backend had no result; do not invent results."

    content = "\n".join(f"- {item}" for item in results[:max_results])
    audit_log("tool_web_search", query=query, backend=backend, result_count=min(len(results), max_results))
    return content[:max_chars]


async def _firecrawl_search(query: str, tool_config: dict[str, Any], httpx_client: Any, max_results: int, timeout: float) -> list[str]:
    api_key_env = tool_config.get("firecrawl_api_key_env", "FIRECRAWL_API_KEY")
    api_key = environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(f"Firecrawl search requires environment variable '{api_key_env}'")

    search_url = validate_outbound_url("https://api.firecrawl.dev/v2/search", tool_config.get("allowed_hosts", ["api.firecrawl.dev"]))
    payload = {
        "query": query,
        "limit": max_results,
        "sources": tool_config.get("sources", ["web"]),
        "timeout": int(timeout * 1000),
        "ignoreInvalidURLs": True,
    }
    if country := tool_config.get("country"):
        payload["country"] = country
    if tbs := tool_config.get("tbs"):
        payload["tbs"] = tbs

    response = await httpx_client.post(
        search_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
        follow_redirects=False,
    )
    response.raise_for_status()
    response_payload = response.json()

    data = response_payload.get("data") or {}
    result_items: list[dict[str, Any]] = []
    for source in ("news", "web"):
        source_results = data.get(source) or []
        if isinstance(source_results, list):
            result_items.extend(item for item in source_results if isinstance(item, dict))

    results = []
    for item in result_items[:max_results]:
        title = item.get("title") or "Untitled"
        description = item.get("description") or item.get("markdown") or ""
        url = item.get("url") or item.get("sourceURL") or ""
        results.append(" - ".join(part for part in (title, description[:240], url) if part))
    return results


async def _duckduckgo_instant_answer_search(query: str, tool_config: dict[str, Any], httpx_client: Any, max_results: int, timeout: float) -> list[str]:
    query_string = urlencode({"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"})
    search_url = validate_outbound_url(f"https://api.duckduckgo.com/?{query_string}", tool_config.get("allowed_hosts", ["api.duckduckgo.com"]))

    response = await httpx_client.get(search_url, timeout=timeout, follow_redirects=False)
    response.raise_for_status()
    payload = response.json()

    results: list[str] = []
    if abstract := payload.get("AbstractText"):
        results.append(f"Answer: {abstract}")
    if answer := payload.get("Answer"):
        results.append(f"Answer: {answer}")

    for topic in payload.get("RelatedTopics", []):
        if isinstance(topic, dict):
            if text := topic.get("Text"):
                results.append(text)
            for nested in topic.get("Topics", []):
                if isinstance(nested, dict) and nested.get("Text"):
                    results.append(nested["Text"])
        if len(results) >= max_results:
            break
    return results


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


async def _rss_feed(arguments: dict[str, Any], tool_config: dict[str, Any], httpx_client: Any) -> str:
    feed_name = str(arguments.get("feed", "")).strip()
    if not feed_name:
        raise RuntimeError("rss_feed requires a non-empty feed name")

    feeds = tool_config.get("feeds") or {}
    feed_config = feeds.get(feed_name)
    if not feed_config:
        raise RuntimeError(f"Unknown RSS feed '{feed_name}'. Available feeds: {', '.join(sorted(feeds)) or 'none'}")

    limit = min(int(arguments.get("limit") or tool_config.get("max_items", 5)), int(tool_config.get("max_items", 5)))
    max_chars = int(tool_config.get("max_response_chars", 4000))
    timeout = float(tool_config.get("timeout_seconds", 10))
    feed_url = validate_outbound_url(str(feed_config.get("url", "")).strip(), tool_config.get("allowed_hosts", []))

    response = await httpx_client.get(feed_url, timeout=timeout, follow_redirects=False)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    items = _rss_items(root)
    if not items:
        return f"RSS feed '{feed_name}' returned no parseable items."

    lines = []
    for item in items[:limit]:
        title = item.get("title") or "Untitled"
        published = item.get("published") or ""
        link = item.get("link") or ""
        summary = item.get("summary") or ""
        lines.append(" - ".join(part for part in (title, published, summary[:220], link) if part))

    audit_log("tool_rss_feed", feed=feed_name, item_count=min(len(items), limit))
    return "\n".join(f"- {line}" for line in lines)[:max_chars]


def _rss_items(root: ET.Element) -> list[dict[str, str]]:
    channel_items = root.findall("./channel/item")
    if channel_items:
        return [_rss_item(item) for item in channel_items]

    atom_ns = {"atom": "http://www.w3.org/2005/Atom"}
    atom_items = root.findall("./atom:entry", atom_ns)
    return [_atom_item(item, atom_ns) for item in atom_items]


def _rss_item(item: ET.Element) -> dict[str, str]:
    published = _text(item, "pubDate")
    return {
        "title": _text(item, "title"),
        "published": _format_date(published),
        "link": _text(item, "link"),
        "summary": _strip_html(_text(item, "description")),
    }


def _atom_item(item: ET.Element, atom_ns: dict[str, str]) -> dict[str, str]:
    link_el = item.find("atom:link", atom_ns)
    published = _text_ns(item, "atom:updated", atom_ns) or _text_ns(item, "atom:published", atom_ns)
    return {
        "title": _text_ns(item, "atom:title", atom_ns),
        "published": _format_date(published),
        "link": link_el.get("href", "") if link_el is not None else "",
        "summary": _strip_html(_text_ns(item, "atom:summary", atom_ns) or _text_ns(item, "atom:content", atom_ns)),
    }


def _text(item: ET.Element, tag: str) -> str:
    child = item.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _text_ns(item: ET.Element, tag: str, ns: dict[str, str]) -> str:
    child = item.find(tag, ns)
    return (child.text or "").strip() if child is not None else ""


def _format_date(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value
