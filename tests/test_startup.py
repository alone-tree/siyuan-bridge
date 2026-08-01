from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from source_code import mcp_server
from source_code.config import Profile
from source_code.ignore import PrivacyRules


class StartupClient:
    def __init__(self):
        self.messages = []

    def version(self):
        return "3.7.2"

    def push_msg(self, message, timeout=7000):
        self.messages.append((message, timeout))


class StartupPacketTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        cache = self.root / "knowledge_base"
        cache.mkdir(parents=True)
        (cache / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "主笔记本"}], ensure_ascii=False),
            encoding="utf-8",
        )
        (cache / "docs.jsonl").write_text(
            json.dumps({
                "id": "doc1",
                "notebook_id": "nb1",
                "notebook_name": "主笔记本",
                "hpath": "/文档",
                "title": "文档",
                "word_count": 10,
                "block_count": 2,
                "updated": "20260701000000",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.original_detect = mcp_server.detect_active_profile
        self.original_load = mcp_server.load_agent_notebook
        self.original_refresh = mcp_server.refresh_index
        self.original_load_live_docs = mcp_server.load_live_docs
        self.client = StartupClient()
        mcp_server.detect_active_profile = lambda _config: (
            Profile(name="当前工作空间", token="test"),
            self.client,
        )
        mcp_server.refresh_index = lambda *_args, **_kwargs: None
        mcp_server.load_live_docs = lambda _client: [
            {"id": "preferences-id", "hpath": "/用户个性化要求"},
            {"id": "mcp-guide-id", "hpath": "/MCP 使用指南"},
            {"id": "index-guide-id", "hpath": "/工作空间索引创建指南"},
            {"id": "index-id", "hpath": "/工作空间索引"},
            {"id": "about-id", "hpath": "/关于思源桥"},
            {"id": "privacy-id", "hpath": "/隐私规则"},
        ]

    def tearDown(self):
        mcp_server.detect_active_profile = self.original_detect
        mcp_server.load_agent_notebook = self.original_load
        mcp_server.refresh_index = self.original_refresh
        mcp_server.load_live_docs = self.original_load_live_docs
        self.temp_dir.cleanup()

    def _state(
        self,
        *,
        placeholder=False,
        updated="20260501000000",
        missing_document_keys=(),
    ):
        return mcp_server.AgentNotebookState(
            language="zh-CN",
            notebook_id="system-nb",
            notebook_name="思源桥",
            document_ids={
                "ai_guide": ("preferences-id",),
                "mcp_usage_guide": ("mcp-guide-id",),
                "workspace_index_guide": ("index-guide-id",),
                "workspace_index": ("index-id",),
                "about": ("about-id",),
                "privacy_rules": ("privacy-id",),
            },
            ai_guide_markdown="用户要求：简洁回答。",
            workspace_index_markdown="# 我的工作空间索引",
            privacy_rules=PrivacyRules(ignore=[], allow=[]),
            mcp_usage_guide_markdown="这是完整 MCP 使用指南。",
            workspace_index_updated=updated,
            workspace_index_is_placeholder=placeholder,
            missing_document_keys=missing_document_keys,
        )

    def test_startup_packet_has_new_sections_in_fixed_order(self):
        mcp_server.load_agent_notebook = (
            lambda *_args, **_kwargs: self._state()
        )

        result = mcp_server.McpServer(self.root).siyuan_start({})

        headings = [
            "## MCP 使用指南",
            "## 用户个性化要求",
            "## 笔记本概览和统计",
            "## 工作空间索引",
        ]
        positions = [result.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("这是完整 MCP 使用指南。", result)
        self.assertIn("用户要求：简洁回答。", result)
        self.assertIn("思源版本：3.7.2", result)
        self.assertIn("隐私规则：已正常加载（无规则）", result)
        self.assertIn("最后更新时间：2026-05-01 00:00", result)
        self.assertNotIn("语言偏好", result)
        self.assertNotIn("给人看的说明", result)
        self.assertNotIn("系统笔记本：", result)

    def test_placeholder_gets_not_created_prompt_without_stale_warning(self):
        mcp_server.load_agent_notebook = (
            lambda *_args, **_kwargs: self._state(
                placeholder=True, updated="20250101000000"
            )
        )

        result = mcp_server.McpServer(self.root).siyuan_start({})

        self.assertIn("用户尚未创建工作空间索引", result)
        self.assertNotIn("工作空间索引已经", result)

    def test_real_old_index_gets_transient_warning(self):
        mcp_server.load_agent_notebook = (
            lambda *_args, **_kwargs: self._state(
                placeholder=False, updated="20250101000000"
            )
        )

        result = mcp_server.McpServer(self.root).siyuan_start({})

        self.assertIn("工作空间索引已经", result)
        self.assertIn("《工作空间索引创建指南》", result)

    def test_missing_non_privacy_document_warns_but_start_continues(self):
        mcp_server.load_agent_notebook = lambda *_args, **_kwargs: self._state(
            missing_document_keys=("about", "ai_guide")
        )

        result = mcp_server.McpServer(self.root).siyuan_start({})

        self.assertIn("# 思源桥启动包", result)
        self.assertIn("## 系统文档警告", result)
        self.assertIn("关于思源桥、用户个性化要求", result)
        self.assertEqual(len(self.client.messages), 1)
        self.assertIn("当前仍可继续使用", self.client.messages[0][0])

    def test_index_age_boundary_is_strictly_more_than_30_days(self):
        now = datetime(2026, 7, 31, 12, 0, 0)

        self.assertEqual(
            mcp_server.workspace_index_age_days("20260702120000", now), 29
        )
        self.assertEqual(
            mcp_server.workspace_index_age_days("20260701120000", now), 30
        )
        self.assertEqual(
            mcp_server.workspace_index_age_days("20260630120000", now), 31
        )


if __name__ == "__main__":
    unittest.main()
