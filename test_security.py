from json import dumps
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import security


class SecurityTests(unittest.TestCase):
    def test_memory_path_rejects_escape(self) -> None:
        with self.assertRaises(ValueError):
            security.memory_path("..", "outside.txt")

    def test_resolve_provider_api_key_from_env(self) -> None:
        with patch.dict("os.environ", {"TEST_OPENAI_KEY": "sk-test-123"}, clear=False):
            api_key = security.resolve_provider_api_key("openai", {"api_key_env": "TEST_OPENAI_KEY"})
        self.assertEqual(api_key, "sk-test-123")

    def test_resolve_provider_api_key_from_codex_auth_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            auth_file = Path(tmp_dir) / "auth.json"
            auth_file.write_text(dumps({"OPENAI_API_KEY": "sk-codex-abc"}), encoding="utf-8")
            api_key = security.resolve_provider_api_key(
                "openai",
                {"auth_mode": "codex_auth_file_api_key", "codex_auth_file": str(auth_file)},
            )
        self.assertEqual(api_key, "sk-codex-abc")

    def test_resolve_provider_key_from_codex_chatgpt_token(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            auth_file = Path(tmp_dir) / "auth.json"
            auth_file.write_text(dumps({"tokens": {"access_token": "chatgpt-access-token"}}), encoding="utf-8")
            api_key = security.resolve_provider_api_key(
                "openai",
                {"auth_mode": "codex_chatgpt_token", "codex_auth_file": str(auth_file)},
            )
        self.assertEqual(api_key, "chatgpt-access-token")


if __name__ == "__main__":
    unittest.main()
