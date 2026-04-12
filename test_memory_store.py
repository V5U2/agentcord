from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import memory_store
import security


class MemoryStoreTests(unittest.TestCase):
    def test_remember_and_forget_round_trip(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "memory"
            with patch.object(security, "MEMORY_ROOT", root):
                remembered = memory_store.remember_facts(
                    42,
                    None,
                    [
                        memory_store.MemoryFact("preferred_name", "James"),
                        memory_store.MemoryFact("likes", "telemetry"),
                    ],
                )
                self.assertTrue(remembered)
                memory_lines = memory_store.list_memories(42, None)
                self.assertTrue(any("preferred_name: James" == line for line in memory_lines))
                removed = memory_store.forget_memories(42, None, "James")
                self.assertEqual(removed, 1)
                self.assertFalse(any("James" in line for line in memory_store.list_memories(42, None)))

    def test_normalize_facts_filters_unknowns_and_duplicates(self) -> None:
        facts = memory_store.normalize_facts(
            [
                {"type": "preferred_name", "value": "James"},
                {"type": "preferred_name", "value": "James"},
                {"type": "unknown", "value": "ignored"},
                {"type": "likes", "value": "telemetry"},
                {"type": "favorite_team", "value": "Ferrari"},
            ]
        )
        self.assertEqual(
            [(fact.fact_type, fact.value) for fact in facts],
            [("preferred_name", "James"), ("likes", "telemetry"), ("favorite_team", "Ferrari")],
        )

    def test_normalize_facts_respects_configured_allowed_types(self) -> None:
        facts = memory_store.normalize_facts(
            [
                {"type": "preferred_name", "value": "James"},
                {"type": "projects", "value": "agentcord"},
            ],
            allowed_fact_types=("projects",),
        )
        self.assertEqual([(fact.fact_type, fact.value) for fact in facts], [("projects", "agentcord")])

    def test_normalize_facts_ignores_non_safe_configured_types(self) -> None:
        facts = memory_store.normalize_facts(
            [{"type": "api_token", "value": "secret"}],
            allowed_fact_types=("api_token",),
        )
        self.assertEqual(facts, [])

    def test_render_memory_grounding_context_includes_recent_messages(self) -> None:
        context = memory_store.render_memory_grounding_context(
            42,
            [
                {"author_id": 11, "content": "What team do you support?"},
                {"author_id": 42, "content": "I support Ferrari."},
            ],
            "My timezone is Australia/Perth",
        )
        self.assertIn("Target user ID: 42", context)
        self.assertIn("11: What team do you support?", context)
        self.assertIn("42: I support Ferrari.", context)
        self.assertIn("My timezone is Australia/Perth", context)


if __name__ == "__main__":
    unittest.main()
