from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from clanker_store import add_clanker, is_clanker, list_clanker_channels, list_clankers, remove_clanker
import security


class ClankerStoreTests(unittest.TestCase):
    def test_add_list_remove_clanker(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            with patch.object(security, "MEMORY_ROOT", Path(tmp_dir) / "memory"):
                add_clanker(100, 200, "RivalBot")
                self.assertTrue(is_clanker(100, 200))
                self.assertEqual(list_clanker_channels(), [100])
                self.assertEqual(list_clankers(100), [(200, "RivalBot")])
                self.assertTrue(remove_clanker(100, 200))
                self.assertFalse(is_clanker(100, 200))


if __name__ == "__main__":
    unittest.main()
