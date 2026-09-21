from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from source_code.config import load_config
from source_code.ignore import PrivacyRules, load_privacy_rules, write_privacy_rules_cache
from source_code.runtime_data import (
    CONFIG_FILE,
    PRIVACY_RULES_FILE,
    SYSTEM_STATE_FILE,
    TELEMETRY_FILE,
    migrate_legacy_runtime_data,
    plugin_data_dir,
    runtime_read_path,
    telemetry_stats_dir,
)
from source_code.system_state import active_system_ids


class RuntimeDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path.cwd() / ".test_tmp" / "runtime_data"
        shutil.rmtree(self.base, ignore_errors=True)
        self.root = self.base / "data" / "plugins" / "siyuan-bridge" / "bridge"
        self.root.mkdir(parents=True)
        self.petal = self.base / "data" / "storage" / "petal" / "siyuan-bridge"

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def test_installed_layout_uses_petal_storage(self) -> None:
        self.assertEqual(plugin_data_dir(self.root), self.petal)
        self.assertEqual(telemetry_stats_dir(self.root), self.petal / "stats")

    def test_development_root_keeps_local_layout(self) -> None:
        development_root = self.base / "checkout"
        development_root.mkdir()
        self.assertEqual(plugin_data_dir(development_root), development_root.absolute())

    def test_installed_layout_survives_junction_without_resolve(self) -> None:
        actual = self.base / "actual-plugin" / "bridge"
        actual.mkdir(parents=True)
        plugins_dir = self.base / "data" / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        installed = plugins_dir / "siyuan-bridge"
        try:
            installed.symlink_to(actual.parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"cannot create junction/symlink: {exc}")
        junction_bridge = installed / "bridge"
        self.assertEqual(plugin_data_dir(junction_bridge), self.petal)
        self.assertNotEqual(plugin_data_dir(junction_bridge.resolve()), self.petal)

    def test_migrates_all_legacy_data_without_overwriting_petal(self) -> None:
        legacy_files = {
            self.root / CONFIG_FILE: "legacy-config",
            self.root / TELEMETRY_FILE: "legacy-telemetry",
            self.root / "knowledge_base" / SYSTEM_STATE_FILE: "legacy-state",
            self.root / "knowledge_base" / PRIVACY_RULES_FILE: "legacy-privacy",
        }
        for path, content in legacy_files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        legacy_event = self.root / "stats" / "events" / "2026-09-21.jsonl"
        legacy_event.parent.mkdir(parents=True)
        legacy_event.write_text("legacy-event\n", encoding="utf-8")

        self.petal.mkdir(parents=True)
        (self.petal / TELEMETRY_FILE).write_text("persistent-telemetry", encoding="utf-8")

        migrated = migrate_legacy_runtime_data(self.root)

        self.assertEqual((self.petal / CONFIG_FILE).read_text(encoding="utf-8"), "legacy-config")
        self.assertEqual((self.petal / TELEMETRY_FILE).read_text(encoding="utf-8"), "persistent-telemetry")
        self.assertEqual((self.petal / SYSTEM_STATE_FILE).read_text(encoding="utf-8"), "legacy-state")
        self.assertEqual((self.petal / PRIVACY_RULES_FILE).read_text(encoding="utf-8"), "legacy-privacy")
        self.assertEqual(
            (self.petal / "stats" / "events" / "2026-09-21.jsonl").read_text(encoding="utf-8"),
            "legacy-event\n",
        )
        self.assertNotIn(self.petal / TELEMETRY_FILE, migrated)
        for path, content in legacy_files.items():
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_runtime_read_prefers_petal_and_migrates_legacy_when_missing(self) -> None:
        legacy_config = self.root / CONFIG_FILE
        legacy_config.write_text("legacy", encoding="utf-8")
        self.petal.mkdir(parents=True)
        persistent_config = self.petal / CONFIG_FILE
        persistent_config.write_text("persistent", encoding="utf-8")

        self.assertEqual(runtime_read_path(self.root, CONFIG_FILE), persistent_config)

        persistent_config.unlink()
        self.assertEqual(runtime_read_path(self.root, CONFIG_FILE), persistent_config)
        self.assertEqual(persistent_config.read_text(encoding="utf-8"), "legacy")

    def test_python_consumers_read_petal_data(self) -> None:
        self.petal.mkdir(parents=True)
        (self.petal / CONFIG_FILE).write_text(
            json.dumps({"profiles": [{"name": "持久工作空间", "token": "petal-token"}], "language": "zh-CN"}),
            encoding="utf-8",
        )
        (self.root / CONFIG_FILE).write_text(
            json.dumps({"profiles": [{"name": "旧工作空间", "token": "legacy-token"}]}),
            encoding="utf-8",
        )
        state = {
            "schema_version": 2,
            "active_workspace_key": "notebook-1",
            "workspaces": {
                "notebook-1": {
                    "system_notebook": {"id": "notebook-1", "name": "思源桥"},
                    "documents": {"privacy_rules": [{"id": "privacy-1"}]},
                }
            },
        }
        (self.petal / SYSTEM_STATE_FILE).write_text(json.dumps(state), encoding="utf-8")

        config = load_config(self.root)
        notebook_id, ids = active_system_ids(self.root)

        self.assertEqual(config.profiles[0].name, "持久工作空间")
        self.assertEqual(config.profiles[0].token, "petal-token")
        self.assertEqual(notebook_id, "notebook-1")
        self.assertEqual(ids["privacy_rules"], {"privacy-1"})

    def test_privacy_rules_cache_is_written_to_petal(self) -> None:
        rules = PrivacyRules(
            ignore=[{"type": "notebook", "id": "hidden-1"}],
            allow=[],
            permissions=[{"type": "document", "id": "doc-1", "permission": "read_only"}],
        )

        write_privacy_rules_cache(self.root, rules)
        loaded = load_privacy_rules(self.root)

        self.assertTrue((self.petal / PRIVACY_RULES_FILE).exists())
        self.assertFalse((self.root / "knowledge_base" / PRIVACY_RULES_FILE).exists())
        self.assertEqual(loaded.ignore, rules.ignore)
        self.assertEqual(loaded.permissions, rules.permissions)


if __name__ == "__main__":
    unittest.main()
