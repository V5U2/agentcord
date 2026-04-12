import unittest

from safe_tools import enabled_tools, execute_tool_call


class SafeToolsTests(unittest.IsolatedAsyncioTestCase):
    def test_disabled_feature_exposes_no_tools(self) -> None:
        config = {"features": {"tools": False}, "tools": {"web_fetch": {"enabled": True}}}
        self.assertEqual(enabled_tools(config), [])

    async def test_disabled_tool_cannot_execute(self) -> None:
        config = {"features": {"tools": True}, "tools": {"web_fetch": {"enabled": False}}}
        with self.assertRaises(RuntimeError):
            await execute_tool_call("web_fetch", '{"url":"https://example.com"}', config, None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
