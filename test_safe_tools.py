import unittest
from unittest.mock import patch

from safe_tools import _rss_items, enabled_tools, execute_tool_call


RSS_FIXTURE = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Story One</title>
      <link>https://example.com/one</link>
      <pubDate>Sun, 12 Apr 2026 00:00:00 GMT</pubDate>
      <description><![CDATA[<p>Summary one.</p>]]></description>
    </item>
  </channel>
</rss>
"""


class SafeToolsTests(unittest.IsolatedAsyncioTestCase):
    def test_disabled_feature_exposes_no_tools(self) -> None:
        config = {"features": {"tools": False}, "tools": {"web_fetch": {"enabled": True}}}
        self.assertEqual(enabled_tools(config), [])

    def test_enabled_rss_feed_exposes_tool(self) -> None:
        config = {"features": {"tools": True}, "tools": {"rss_feed": {"enabled": True, "feeds": {"bbc_world": {"url": "https://feeds.bbci.co.uk/news/world/rss.xml"}}}}}
        tool_names = [tool["function"]["name"] for tool in enabled_tools(config)]
        self.assertIn("rss_feed", tool_names)

    def test_openrouter_server_search_exposes_server_tool(self) -> None:
        config = {"features": {"tools": True}, "tools": {"web_search": {"enabled": True, "backend": "openrouter_server", "engine": "auto", "max_results": 5}}}
        tools = enabled_tools(config, "openrouter")
        self.assertEqual(tools[0]["type"], "openrouter:web_search")
        self.assertEqual(tools[0]["parameters"]["engine"], "auto")

    async def test_disabled_tool_cannot_execute(self) -> None:
        config = {"features": {"tools": True}, "tools": {"web_fetch": {"enabled": False}}}
        with self.assertRaises(RuntimeError):
            await execute_tool_call("web_fetch", '{"url":"https://example.com"}', config, None)  # type: ignore[arg-type]

    async def test_firecrawl_requires_api_key(self) -> None:
        config = {
            "features": {"tools": True},
            "tools": {"web_search": {"enabled": True, "backend": "firecrawl", "firecrawl_api_key_env": "MISSING_FIRECRAWL_KEY"}},
        }
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                await execute_tool_call("web_search", '{"query":"latest news"}', config, None)  # type: ignore[arg-type]

    def test_rss_fixture_parses_items(self) -> None:
        from defusedxml import ElementTree as ET

        items = _rss_items(ET.fromstring(RSS_FIXTURE))
        self.assertEqual(items[0]["title"], "Story One")
        self.assertEqual(items[0]["summary"], "Summary one.")


if __name__ == "__main__":
    unittest.main()
