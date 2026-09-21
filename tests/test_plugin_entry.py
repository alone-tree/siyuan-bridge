from __future__ import annotations

import re
import unittest
from pathlib import Path


INDEX_JS = Path(__file__).resolve().parents[1] / "siyuan-plugin" / "index.js"
SRC_INDEX_JS = Path(__file__).resolve().parents[1] / "siyuan-plugin" / "src" / "index.js"


class PluginEntryContractTests(unittest.TestCase):
    def test_root_index_is_single_file_commonjs(self):
        text = INDEX_JS.read_text(encoding="utf-8")
        requires = re.findall(r"\brequire\((['\"])(.+?)\1\)", text)
        self.assertEqual(
            {module for _quote, module in requires},
            {"siyuan"},
            "根 index.js 只能 require(\"siyuan\")，不能拆本地 JS 模块，否则设置齿轮消失。",
        )
        self.assertIsNone(
            re.search(r"(?m)^\s*import\s", text),
            "根 index.js 禁止 ESM import。",
        )
        self.assertIn("module.exports = SiyuanBridgePlugin", text)

    def test_runtime_data_uses_plugin_storage_with_legacy_migration(self):
        text = INDEX_JS.read_text(encoding="utf-8")
        self.assertIn('const CONFIG_STORAGE = "config.local.json"', text)
        self.assertIn('const TELEMETRY_STORAGE = "telemetry.json"', text)
        self.assertIn('const SYSTEM_STATE_STORAGE = "system_state.json"', text)
        self.assertIn("await plugin.loadData(storageName)", text)
        self.assertIn("await plugin.saveData(storageName, parsed)", text)
        self.assertIn("await plugin.saveData(CONFIG_STORAGE, normalized)", text)
        self.assertIn("await plugin.saveData(TELEMETRY_STORAGE, existing)", text)
        self.assertIn("await plugin.saveData(SYSTEM_STATE_STORAGE, state)", text)
        self.assertIn("TELEMETRY_ID_PATH", text)
        self.assertIn("LEGACY_TELEMETRY_ID_PATH", text)
        self.assertIn("await loadTelemetryIdFallback()", text)
        self.assertNotIn("putFile(CONFIG_PATH", text)
        self.assertNotIn("putFile(TELEMETRY_PATH", text)
        self.assertNotIn("putFile(SYSTEM_STATE_PATH", text)

    def test_privacy_registry_is_persisted_before_optional_guides(self):
        text = INDEX_JS.read_text(encoding="utf-8")
        start = text.index("async function ensureSystemNotebook(plugin)")
        end = text.index("async function loadSystemState(plugin)", start)
        lifecycle = text[start:end]
        privacy = lifecycle.index('"privacy_rules"')
        first_persist = lifecycle.index("await persistState();")
        managed_guide = lifecycle.index("const maintenanceSteps")
        self.assertLess(privacy, first_persist)
        self.assertLess(first_persist, managed_guide)
        self.assertIn("reconcileSystemDocumentRegistry(documentCache, rescanned)", lifecycle)

    def test_managed_template_hash_normalizes_line_endings(self):
        root_text = INDEX_JS.read_text(encoding="utf-8")
        source_text = SRC_INDEX_JS.read_text(encoding="utf-8")
        marker = "sha256Text(normalizeLineEndings(template))"
        self.assertIn(marker, root_text)
        self.assertIn(marker, source_text)

    def test_reference_source_contains_runtime_storage_contract(self):
        source_text = SRC_INDEX_JS.read_text(encoding="utf-8")
        for marker in (
            "async function loadPluginData(plugin, storageName, legacyPath)",
            "async function ensureSystemNotebook(plugin)",
            "await plugin.saveData(CONFIG_STORAGE, normalized)",
            "await plugin.saveData(TELEMETRY_STORAGE, existing)",
            "await plugin.saveData(SYSTEM_STATE_STORAGE, state)",
            "async function loadTelemetryIdFallback()",
            "await loadTelemetryIdFallback()",
        ):
            self.assertIn(marker, source_text)
