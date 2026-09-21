from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


JS_TEST = Path(__file__).resolve().parent / "test_plugin_storage.js"


class PluginStorageTests(unittest.TestCase):
    def test_javascript_storage_migration(self) -> None:
        completed = subprocess.run(
            ["node", str(JS_TEST)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            self.fail(
                "JS plugin storage test failed:\n"
                + (completed.stdout or "")
                + (completed.stderr or "")
            )


if __name__ == "__main__":
    unittest.main()
