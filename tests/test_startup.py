from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from source_code import mcp_server
from source_code.config import Profile
from source_code.ignore import PrivacyRules
from source_code.i18n import WORKSPACE_INDEX_PLACEHOLDERS


class StartupClient:
    def __init__(self):
        self.messages = []
        self.blocks = {}

    def list_notebooks(self):
        return [{"id": "nb1", "name": "主笔记本"}, {"id": "system-nb", "name": "思源桥"}]

    def get_child_blocks(self, doc_id):
        return self.blocks.get(doc_id, [])

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
        with (cache / "docs.jsonl").open("a", encoding="utf-8") as target:
            for doc_id, title in (("preferences-id", "用户个性化要求"), ("index-id", "工作空间索引")):
                target.write(json.dumps({"id": doc_id, "notebook_id": "system-nb", "notebook_name": "思源桥",
                                         "hpath": "/" + title, "title": title}, ensure_ascii=False) + "\n")
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
        self.client.blocks = {
            "preferences-id": [{"id": "pref-p", "type": "p", "markdown": "用户要求：简洁回答。"}],
            "index-id": [{"id": "index-p", "type": "p", "markdown":
                          WORKSPACE_INDEX_PLACEHOLDERS["zh-CN"].strip() if placeholder else "# 我的工作空间索引"}],
        }
        return mcp_server.AgentNotebookState(
            language="zh-CN",
            notebook_id="system-nb",
            notebook_name="思源桥",
            document_ids={
                "ai_guide": ("preferences-id",),
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
            "## 运行状态与程序数据",
            "## 工作空间索引",
            "## 用户个性化要求",
            "## 思源桥使用指南",
            "### 工作空间索引创建指南",
        ]
        positions = [result.splitlines().index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(
            [line for line in result.splitlines() if line.startswith("## ")],
            ["## 运行状态与程序数据", "## 工作空间索引", "## 用户个性化要求", "## 思源桥使用指南"],
        )
        self.assertIn("### 我的工作空间索引", result)
        self.assertIn("这是完整 MCP 使用指南。", result)
        self.assertIn("用户要求：简洁回答。", result)
        self.assertIn(f"思源版本：3.7.2\n\n思源桥MCP版本：{mcp_server.__version__}", result)
        self.assertIn("隐私规则：已正常加载（无规则）", result)
        self.assertIn("最后更新时间：2026-05-01 00:00", result)
        self.assertNotIn("语言偏好", result)
        self.assertNotIn("给人看的说明", result)
        self.assertNotIn("系统笔记本：", result)
        self.assertIn("| 笔记本 | ID | 文档数 | 字数 | 块数 | 最近更新 |", result)
        self.assertIn('字数、块数统计时间：未记录。需要更新时调用 siyuan_operate(action="refresh")。', result)
        self.assertIn('siyuan_operate(action="read_guide", guide="workspace_index")', result)
        self.assertNotIn("templates", result)

    def test_start_requests_metadata_only_before_rendering(self):
        mcp_server.load_agent_notebook = lambda *_args, **_kwargs: self._state()
        with mock.patch.object(mcp_server, "refresh_index") as refresh:
            mcp_server.McpServer(self.root).siyuan_start({})
        self.assertIs(refresh.call_args.kwargs["include_stats"], False)


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
        self.assertIn('siyuan_operate(action="read_guide", guide="workspace_index")', result)

    def test_missing_non_privacy_document_warns_but_start_continues(self):
        mcp_server.load_agent_notebook = lambda *_args, **_kwargs: self._state(
            missing_document_keys=("about", "ai_guide")
        )

        result = mcp_server.McpServer(self.root).siyuan_start({})

        self.assertIn("# 思源桥启动包", result)
        self.assertIn("### 系统文档警告", result)
        self.assertLess(result.index("## 运行状态与程序数据"), result.index("### 系统文档警告"))
        self.assertLess(result.splitlines().index("### 系统文档警告"), result.splitlines().index("## 工作空间索引"))
        self.assertIn("关于思源桥、用户个性化要求", result)
        self.assertEqual(len(self.client.messages), 1)
        self.assertIn("当前仍可继续使用", self.client.messages[0][0])

    def test_same_name_documents_nest_each_own_heading_tree_without_mutating_sources(self):
        state = self._state()
        state.document_ids["workspace_index"] = ("index-id", "index-2")
        self.client.blocks["index-id"] = [{"id": "a", "type": "p", "markdown": "## 第一篇\n### 第一子节"}]
        self.client.blocks["index-2"] = [{"id": "b", "type": "p", "markdown": "# 第二篇\n## 第二子节"}]
        path = self.root / "knowledge_base" / "docs.jsonl"
        with path.open("a", encoding="utf-8") as target:
            target.write(json.dumps({"id": "index-2", "notebook_id": "system-nb", "notebook_name": "思源桥",
                                     "hpath": "/工作空间索引", "title": "工作空间索引"}, ensure_ascii=False) + "\n")
        original_blocks = json.dumps(self.client.blocks)
        result = mcp_server.McpServer(self.root)._render_startup_documents(self.client, state)
        self.assertEqual(result.workspace_index_markdown,
                         "### 第一篇\n#### 第一子节\n\n---\n\n### 第二篇\n#### 第二子节")
        self.assertEqual(json.dumps(self.client.blocks), original_blocks)

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


class StartupHeadingTests(unittest.TestCase):
    def test_preserves_relative_heading_depth_and_clamps_to_six(self):
        result = mcp_server.nest_startup_headings("## 章\n### 节\n#### 小节\n###### 深层\n")
        self.assertEqual(result, "### 章\n#### 节\n##### 小节\n###### 深层\n")

    def test_fenced_indented_and_quoted_headings_do_not_affect_nesting(self):
        body = ("## 真实标题\n```markdown\n# 代码示例\n```\n"
                "~~~text\n# 另一示例\n~~~\n    # 缩进代码\n\t# 制表符代码\n"
                "> # 引用标题\n> > ## 嵌套引用标题\n### 真实子节\n")
        result = mcp_server.nest_startup_headings(body)
        self.assertEqual(result, body.replace("## 真实标题", "### 真实标题").replace("### 真实子节", "#### 真实子节"))

    def test_longer_fence_with_shorter_inner_marker_stays_open(self):
        body = "````markdown\n```\n# 代码标题\n````\n## 正文标题\n"
        self.assertEqual(mcp_server.nest_startup_headings(body), body.replace("## 正文标题", "### 正文标题"))

    def test_body_without_headings_is_unchanged(self):
        body = "普通正文\n\n> ## 引用\n\n    # 代码\n"
        self.assertEqual(mcp_server.nest_startup_headings(body), body)


if __name__ == "__main__":
    unittest.main()
