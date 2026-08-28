from __future__ import annotations

import re
import unittest
from pathlib import Path


INDEX_JS = Path(__file__).resolve().parents[1] / "siyuan-plugin" / "index.js"


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
