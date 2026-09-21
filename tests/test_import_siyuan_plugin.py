from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import import_siyuan_plugin as importer  # noqa: E402


class ImportSiyuanPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path.cwd() / ".test_tmp" / "import_siyuan_plugin"
        shutil.rmtree(self.base, ignore_errors=True)
        self.plugins_dir = self.base / "data" / "plugins"
        self.target = self.plugins_dir / importer.PLUGIN_NAME
        self.plugin_data = self.base / "data" / "storage" / "petal" / importer.PLUGIN_NAME
        self.target.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def test_resolves_plugin_data_directory(self) -> None:
        self.assertEqual(importer.resolve_plugin_data_dir(self.plugins_dir), self.plugin_data)

    def test_normal_import_migrates_legacy_data_without_overwrite(self) -> None:
        legacy = {
            self.target / "bridge" / "config.local.json": "legacy-config",
            self.target / "bridge" / "telemetry.json": "legacy-telemetry",
            self.target / "bridge" / "knowledge_base" / "system_state.json": "legacy-state",
            self.target / "bridge" / "knowledge_base" / "privacy_rules.json": "legacy-privacy",
        }
        for path, content in legacy.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        legacy_event = self.target / "bridge" / "stats" / "events" / "event.jsonl"
        legacy_event.parent.mkdir(parents=True)
        legacy_event.write_text("legacy-event", encoding="utf-8")
        self.plugin_data.mkdir(parents=True)
        (self.plugin_data / "telemetry.json").write_text("persistent-telemetry", encoding="utf-8")

        importer.migrate_legacy_runtime_data(self.target, self.plugin_data)

        self.assertEqual((self.plugin_data / "config.local.json").read_text(encoding="utf-8"), "legacy-config")
        self.assertEqual((self.plugin_data / "telemetry.json").read_text(encoding="utf-8"), "persistent-telemetry")
        self.assertEqual((self.plugin_data / "system_state.json").read_text(encoding="utf-8"), "legacy-state")
        self.assertEqual((self.plugin_data / "privacy_rules.json").read_text(encoding="utf-8"), "legacy-privacy")
        self.assertEqual(
            (self.plugin_data / "stats" / "events" / "event.jsonl").read_text(encoding="utf-8"),
            "legacy-event",
        )

    def test_fresh_removes_plugin_and_petal_data(self) -> None:
        (self.target / "index.js").write_text("plugin", encoding="utf-8")
        self.plugin_data.mkdir(parents=True)
        (self.plugin_data / "config.local.json").write_text("config", encoding="utf-8")

        importer.remove_target(self.target, self.plugins_dir)
        importer.remove_plugin_data(self.plugin_data, self.plugins_dir)

        self.assertFalse(self.target.exists())
        self.assertFalse(self.plugin_data.exists())

    def test_remove_target_rejects_another_plugin_directory(self) -> None:
        other_plugin = self.plugins_dir / "other-plugin"
        other_plugin.mkdir()

        with self.assertRaises(SystemExit):
            importer.remove_target(other_plugin, self.plugins_dir)

        self.assertTrue(other_plugin.exists())

    def test_remove_plugin_data_rejects_unexpected_path(self) -> None:
        with self.assertRaises(SystemExit):
            importer.remove_plugin_data(self.base / "unexpected", self.plugins_dir)


if __name__ == "__main__":
    unittest.main()
