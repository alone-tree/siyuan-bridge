from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_package  # noqa: E402


class BuildPackageTests(unittest.TestCase):
    def test_all_runtime_data_is_excluded_from_bridge(self) -> None:
        self.assertTrue(
            {
                "knowledge_base",
                "stats",
                "config.local.json",
                "telemetry.json",
                "system_state.json",
                "privacy_rules.json",
            }.issubset(build_package.BRIDGE_RUNTIME_NAMES)
        )


if __name__ == "__main__":
    unittest.main()
