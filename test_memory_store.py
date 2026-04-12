from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import memory_store
import security


class MemoryStoreTests(unittest.TestCase):
    def test_extract_facts_limits_to_allowed_patterns(self) -> None:
        facts = memory_store.extract_facts("My name is James and I like Formula 1")
        self.assertEqual([fact.fact_type for fact in facts], ["preferred_name", "likes"])

    def test_remember_and_forget_round_trip(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "memory"
            with patch.object(security, "MEMORY_ROOT", root):
                remembered = memory_store.remember_text(42, None, "My name is James and I like telemetry")
                self.assertTrue(remembered)
                memory_lines = memory_store.list_memories(42, None)
                self.assertTrue(any("preferred_name: James" == line for line in memory_lines))
                removed = memory_store.forget_memories(42, None, "James")
                self.assertEqual(removed, 1)
                self.assertFalse(any("James" in line for line in memory_store.list_memories(42, None)))


if __name__ == "__main__":
    unittest.main()
