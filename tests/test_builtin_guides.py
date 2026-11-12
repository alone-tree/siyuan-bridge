from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from source_code.mcp_server import McpServer


class BuiltinGuideTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "中文 workspace/data/plugins/siyuan-bridge/bridge"
        self.guides = self.root / "templates/guides"
        shutil.copytree(Path(__file__).absolute().parents[1] / "templates/guides", self.guides)
        self.server = McpServer(self.root)
        # 指南读取只需已初始化的会话，不应调用思源 API。
        self.server._active_client = mock.Mock(spec=[])

    def test_complete_guides_are_read_from_install_root_in_startup_language(self):
        for language in ("zh-CN", "en"):
            self.server._active_language = language
            for guide, filename in (("mcp_usage", "mcp-usage-guide"), ("workspace_index", "workspace-index-guide")):
                with self.subTest(language=language, guide=guide):
                    response = self.server.call_tool(1, "siyuan_operate", {"action": "read_guide", "guide": guide})
                    self.assertFalse(response["result"].get("isError", False))
                    self.assertEqual(
                        response["result"]["content"][0]["text"],
                        (self.guides / f"{filename}.{language}.md").read_text(encoding="utf-8"),
                    )
        self.assertEqual(self.server._active_client.mock_calls, [])

    def test_invalid_or_missing_guide_returns_valid_call_without_reading_files(self):
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("不应读取文件")):
            for guide in ("", "../config.local.json", "unknown"):
                with self.subTest(guide=guide), self.assertRaises(ValueError) as error:
                    self.server.siyuan_operate({"action": "read_guide", "guide": guide})
                self.assertIn('guide="workspace_index"', str(error.exception))

    def test_missing_file_returns_explicit_tool_error(self):
        (self.guides / "workspace-index-guide.zh-CN.md").unlink()
        response = self.server.call_tool(1, "siyuan_operate", {"action": "read_guide", "guide": "workspace_index"})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("无法读取内置指南 workspace_index", response["result"]["content"][0]["text"])

    def test_read_guide_keeps_start_requirement(self):
        self.server._clear_active_connection()
        self.assertIsNone(self.server._active_language)
        response = self.server.call_tool(1, "siyuan_operate", {"action": "read_guide", "guide": "workspace_index"})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("请先调用 siyuan_start", response["result"]["content"][0]["text"])
