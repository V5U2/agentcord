import unittest

from security import is_wake_name_match


class WakeNameTests(unittest.TestCase):
    def test_matches_configured_name_with_boundaries(self) -> None:
        self.assertTrue(is_wake_name_match("agentcord, can you help?", ["agentcord"]))
        self.assertTrue(is_wake_name_match("hey AgentCord", ["agentcord"]))

    def test_does_not_match_inside_longer_word(self) -> None:
        self.assertFalse(is_wake_name_match("superagentcordbot", ["agentcord"]))

    def test_empty_names_do_not_match(self) -> None:
        self.assertFalse(is_wake_name_match("hello", [""]))


if __name__ == "__main__":
    unittest.main()
