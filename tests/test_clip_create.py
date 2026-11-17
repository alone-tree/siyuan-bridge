"""siyuan_create 的 source_url 接入：三选一、抓取时机、原文引用块与图片本地化。"""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from source_code import mcp_server
from source_code.clip import ClipError, ClippedPage
from source_code.config import Profile
from source_code.ignore import PrivacyRules, write_privacy_rules_cache
from tests.test_mcp_server import FakeSearchClient


class SourceUrlCreateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / ".test_tmp" / "clip_create"
        shutil.rmtree(self.root, ignore_errors=True)
        base = self.root / "knowledge_base"
        base.mkdir(parents=True, exist_ok=True)
        (base / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "Main"}], ensure_ascii=False), encoding="utf-8"
        )
        write_privacy_rules_cache(self.root, PrivacyRules(ignore=[], allow=[]))
        (base / "docs.jsonl").write_text("", encoding="utf-8")
        self._original_load_agent_notebook = mcp_server.load_agent_notebook
        mcp_server.load_agent_notebook = self._fake_agent_notebook

    def tearDown(self):
        mcp_server.load_agent_notebook = self._original_load_agent_notebook

    @staticmethod
    def _fake_agent_notebook(_client, _root, config_language=None):
        return mcp_server.AgentNotebookState(
            language=config_language or "zh-CN",
            notebook_id="system-nb",
            notebook_name="思源桥",
            document_ids={
                "ai_guide": (), "mcp_usage_guide": (), "workspace_index_guide": (),
                "workspace_index": (), "about": (), "privacy_rules": (),
            },
            ai_guide_markdown="",
            workspace_index_markdown="",
            privacy_rules=PrivacyRules(ignore=[], allow=[]),
        )

    def _server(self):
        client = FakeSearchClient([])
        client.extension_copy = lambda dom, href, *, clip_type="article", assets=False: ""
        client.net_img2_local_assets = lambda doc_id: None
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        return server, client

    def _patch_clip(self, *, title="页面标题", markdown="# 页面标题\n\n正文内容", url="https://example.com/a"):
        page = ClippedPage(url=url, title=title, html="<body><p>正文内容</p></body>")
        return mock.patch.object(mcp_server, "clip_url", return_value=page), markdown

    def test_source_url_writes_document_with_source_line(self):
        server, client = self._server()
        patcher, markdown = self._patch_clip()
        with patcher, mock.patch.object(client, "extension_copy", return_value=markdown):
            result = server.siyuan_create({
                "source_url": "https://example.com/a",
                "path": "/Main/文章",
                "confirmed": True,
            })
        self.assertIn("文档写入成功", result)
        notebook, path, written = client._created_docs[0]
        self.assertEqual(path, "/文章")
        # 原文引用块在最前，重复 H1 已被去掉
        self.assertTrue(written.startswith("> 原文：https://example.com/a"))
        self.assertNotIn("# 页面标题", written)
        self.assertIn("正文内容", written)
        self.assertIn("剪藏来源", result)
        self.assertIn("图片本地化", result)

    def test_source_url_uses_page_title_when_title_omitted(self):
        server, client = self._server()
        patcher, markdown = self._patch_clip(title="抓来的标题")
        with patcher, mock.patch.object(client, "extension_copy", return_value=markdown):
            result = server.siyuan_create({
                "source_url": "https://example.com/a",
                "path": "/Main/文章",
                "confirmed": True,
            })
        self.assertIn("抓来的标题", result)

    def test_explicit_title_wins(self):
        server, client = self._server()
        patcher, markdown = self._patch_clip(title="页面标题")
        with patcher, mock.patch.object(client, "extension_copy", return_value=markdown):
            result = server.siyuan_create({
                "source_url": "https://example.com/a",
                "title": "我自己起的标题",
                "path": "/Main/文章",
                "confirmed": True,
            })
        self.assertIn("我自己起的标题", result)
        self.assertIn("页面原标题", result)

    def test_fetch_failure_writes_nothing(self):
        server, client = self._server()
        with mock.patch.object(mcp_server, "clip_url", side_effect=ClipError("抓不到", code="api:clip_fetch_failed")):
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "source_url": "https://example.com/a",
                    "path": "/Main/文章",
                    "confirmed": True,
                })
        self.assertEqual(getattr(ctx.exception, "error_code", ""), "api:clip_fetch_failed")
        self.assertEqual(client._created_docs, [])
        self.assertEqual(client._snapshots, [])

    def test_blocked_page_writes_nothing(self):
        server, client = self._server()
        with mock.patch.object(mcp_server, "clip_url", side_effect=ClipError("验证页", code="api:clip_blocked")):
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "source_url": "https://example.com/a",
                    "path": "/Main/文章",
                    "confirmed": True,
                })
        self.assertEqual(getattr(ctx.exception, "error_code", ""), "api:clip_blocked")
        self.assertEqual(client._snapshots, [])

    def test_convert_failure_writes_nothing(self):
        server, client = self._server()
        patcher, _ = self._patch_clip()
        with patcher, mock.patch.object(
            client, "extension_copy", side_effect=mcp_server.SiYuanApiError("boom")
        ):
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "source_url": "https://example.com/a",
                    "path": "/Main/文章",
                    "confirmed": True,
                })
        self.assertEqual(getattr(ctx.exception, "error_code", ""), "api:clip_convert_failed")
        self.assertEqual(client._snapshots, [])

    def test_three_sources_are_mutually_exclusive(self):
        server, _client = self._server()
        with self.assertRaises(ValueError) as ctx:
            server.siyuan_create({
                "markdown": "x",
                "source_url": "https://example.com/a",
                "title": "t",
                "path": "/Main/文章",
                "confirmed": True,
            })
        self.assertEqual(getattr(ctx.exception, "error_code", ""), "validation:markdown_conflict")

    def test_image_localization_failure_keeps_document(self):
        server, client = self._server()
        patcher, markdown = self._patch_clip()
        with patcher, mock.patch.object(client, "extension_copy", return_value=markdown), \
                mock.patch.object(client, "net_img2_local_assets", side_effect=RuntimeError("下载失败")):
            result = server.siyuan_create({
                "source_url": "https://example.com/a",
                "path": "/Main/文章",
                "confirmed": True,
            })
        self.assertIn("文档写入成功", result)
        self.assertIn("本地化失败", result)
        self.assertIn("保留外链", result)
        self.assertEqual(len(client._created_docs), 1)

    def test_edit_rejects_source_url(self):
        from source_code.mcp_server import resolve_markdown_input
        with self.assertRaises(ValueError) as ctx:
            resolve_markdown_input({"source_url": "https://example.com/a"}, require=True)
        self.assertIn("只有 siyuan_create 支持", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
