from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from typing import Any

from source_code import mcp_server
from source_code.ignore import PrivacyRules, write_privacy_rules_cache
from source_code.inbox import (
    collect_shorthands,
    find_entry,
    inbox_notebook_name,
    split_inbox_path,
    supports_create_doc_by_parent,
    title_matches,
    writable_document_title,
)


def make_entry(
    oid: str,
    title: str,
    markdown: str = "正文",
    created: str = "2026-11-17 09:56",
    url: str = "https://example.com/a",
) -> dict[str, Any]:
    return {
        "oId": oid,
        "shorthandTitle": title,
        "shorthandMd": markdown,
        "hCreated": created,
        "shorthandURL": url,
    }


class FakeInboxClient:
    """只实现收集箱路径需要的客户端方法。"""

    def __init__(
        self,
        entries: list[dict[str, Any]] | None = None,
        *,
        version: str = "3.0.0",
        page_size: int = 2,
        fail: bool = False,
        snapshot_error: str = "",
    ):
        self.entries = list(entries or [])
        self._version = version
        self.page_size = max(page_size, 1)
        self.fail = fail
        self.snapshot_error = snapshot_error
        self.removed: list[str] = []
        self.created_with_md: list[tuple[str, str, str]] = []
        self.created_by_parent: list[tuple[str, str | None, str, str, str]] = []
        self.snapshots: list[str] = []
        self.pushed: list[str] = []

    def version(self) -> str:
        return self._version

    def _page(self, page: int) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("cloud unreachable")
        total = len(self.entries)
        page_count = (total + self.page_size - 1) // self.page_size
        start = (page - 1) * self.page_size
        chunk = self.entries[start:start + self.page_size] if page <= page_count else []
        return {
            "pagination": {
                "paginationPageCount": page_count,
                "paginationRecordCount": total,
                "paginationPageNums": list(range(1, page_count + 1)),
            },
            "shorthands": chunk,
        }

    def get_shorthands(self, page: int) -> dict[str, Any]:
        return self._page(page)

    def get_shorthand(self, oid: str) -> dict[str, Any]:
        for entry in self.entries:
            if entry.get("oId") == oid:
                return dict(entry)
        raise RuntimeError("not found")

    def remove_shorthands(self, ids: list[str]) -> None:
        self.removed.extend(ids)

    def create_doc_with_md(self, notebook: str, path: str, markdown: str) -> dict[str, Any]:
        self.created_with_md.append((notebook, path, markdown))
        return {"id": "created-0001"}

    def create_doc_by_parent(
        self,
        notebook: str,
        parent_id: str | None,
        doc_id: str,
        title: str,
        markdown: str,
    ) -> dict[str, Any]:
        self.created_by_parent.append((notebook, parent_id, doc_id, title, markdown))
        return {"id": doc_id, "document": {}}

    def create_snapshot(self, memo: str) -> None:
        if self.snapshot_error:
            raise mcp_server.SiYuanApiError(self.snapshot_error)
        self.snapshots.append(memo)

    def push_msg(self, msg: str, timeout: int = 7000) -> None:
        self.pushed.append(msg)


class InboxPureTests(unittest.TestCase):
    def test_notebook_name_follows_language_and_avoids_conflicts(self):
        self.assertEqual("收集箱", inbox_notebook_name("zh-CN", ["Main"]))
        self.assertEqual("收集箱(系统)", inbox_notebook_name("zh-CN", ["收集箱"]))
        self.assertEqual("收集箱(系统)(2)", inbox_notebook_name("zh-CN", ["收集箱", "收集箱(系统)"]))
        self.assertEqual("Inbox", inbox_notebook_name("en", ["Main"]))
        self.assertEqual("Inbox(System)", inbox_notebook_name("en", ["Inbox"]))

    def test_split_inbox_path_only_splits_first_segment(self):
        self.assertEqual("A/B", split_inbox_path("/收集箱/A/B", "收集箱"))
        self.assertEqual("", split_inbox_path("/收集箱", "收集箱"))
        self.assertEqual("", split_inbox_path("/收集箱/", "收集箱"))
        self.assertIsNone(split_inbox_path("/Main/A", "收集箱"))
        self.assertIsNone(split_inbox_path("", "收集箱"))

    def test_title_matches_query_and_regex(self):
        self.assertTrue(title_matches("GPU 光模块 调研", "query", "gpu 调研"))
        self.assertFalse(title_matches("GPU 光模块", "query", "GPU NVLink"))
        self.assertTrue(title_matches("朋友圈反着发", "regex", "朋友圈.*发"))
        self.assertFalse(title_matches("朋友圈", "regex", "["))

    def test_writable_title_replaces_halfwidth_slash(self):
        self.assertEqual("A／B", writable_document_title("A/B"))
        self.assertEqual("A B", writable_document_title("A\nB"))

    def test_version_branch_boundary(self):
        self.assertFalse(supports_create_doc_by_parent("3.3.0"))
        self.assertFalse(supports_create_doc_by_parent("3.8.8"))
        self.assertTrue(supports_create_doc_by_parent("3.8.9"))
        self.assertTrue(supports_create_doc_by_parent("3.8.10"))
        self.assertTrue(supports_create_doc_by_parent("4.0.0"))

    def test_find_entry_prefers_id_then_unique_title(self):
        entries = [make_entry("1794880590020", "同名"), make_entry("1794880590021", "同名")]
        self.assertEqual("1794880590020", find_entry(entries, "1794880590020")[0]["oId"])
        entry, matches = find_entry(entries, "同名")
        self.assertIsNone(entry)
        self.assertEqual(2, len(matches))

    def test_collect_shorthands_reports_cloud_total(self):
        client = FakeInboxClient(
            [make_entry("1", "甲"), make_entry("2", "乙"), make_entry("3", "丙")],
            page_size=1,
        )
        entries, total = collect_shorthands(client, 1)
        self.assertEqual(1, len(entries))
        self.assertEqual(3, total)


class InboxServerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / ".test_tmp" / "inbox"
        shutil.rmtree(self.root, ignore_errors=True)
        base = self.root / "knowledge_base"
        base.mkdir(parents=True, exist_ok=True)
        (base / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "Main"}], ensure_ascii=False),
            encoding="utf-8",
        )
        docs = [
            {
                "id": "doc1",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Projects/Doc One",
                "title": "Doc One",
                "path": "/doc1.sy",
                "tags": [],
                "word_count": 10,
                "block_count": 2,
                "updated": "20260501010101",
            },
        ]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs),
            encoding="utf-8",
        )
        write_privacy_rules_cache(self.root, PrivacyRules(ignore=[], allow=[]))

    def _server(self, entries: list[dict[str, Any]] | None = None, **kwargs):
        client = FakeInboxClient(entries, **kwargs)
        server = mcp_server.McpServer(self.root)
        server._active_client = client
        server._active_language = "zh-CN"
        server._refresh_index_with_system_context = lambda _client: None  # type: ignore[assignment]
        server._wait_for_hpath = lambda *_args, **_kwargs: mcp_server.PostWriteSyncStatus(True, "路径已同步")  # type: ignore[assignment]
        return server, client

    def test_notebook_list_appends_inbox_row(self):
        server, _ = self._server([make_entry("1794880590020", "甲")])
        result = server.siyuan_list({})
        self.assertIn("| 收集箱 | - | read_only |", result)

    def test_notebook_list_skips_empty_or_failed_inbox(self):
        server, _ = self._server([])
        self.assertNotIn("收集箱", server.siyuan_list({}))
        server, _ = self._server([make_entry("1", "甲")], fail=True)
        self.assertNotIn("收集箱", server.siyuan_list({}))

    def test_list_inbox_renders_rows_and_pagination(self):
        entries = [
            make_entry("1794880590020", "A/B 标题", created="2026-11-17 09:56"),
            make_entry("1792243354103", "第二条", created="2026-10-17 21:22"),
            make_entry("1792243354104", "第三条"),
        ]
        server, _ = self._server(entries, page_size=2)
        result = server.siyuan_list({"path": "/收集箱", "limit": 2, "offset": 0})
        self.assertIn("| document | document_id | 权限 | 收藏时间 | 来源 |", result)
        self.assertIn("/收集箱/A/B 标题", result)
        self.assertIn("`1794880590020`", result)
        self.assertIn("2026-11-17 09:56", result)
        self.assertIn("还有 1 条未显示", result)
        page2 = server.siyuan_list({"path": "/收集箱", "limit": 2, "offset": 2})
        self.assertIn("第三条", page2)
        self.assertNotIn("还有 1 条未显示", page2)

    def test_list_inbox_rejects_entry_path(self):
        server, _ = self._server([make_entry("1", "甲")])
        with self.assertRaisesRegex(ValueError, "没有子文档"):
            server.siyuan_list({"path": "/收集箱/甲"})

    def test_read_by_path_keeps_slash_titles_intact(self):
        server, _ = self._server([make_entry("1794880590020", "A/B 标题", markdown="# 正文\n\n内容")])
        result = server.siyuan_read({"document_path": "/收集箱/A/B 标题"})
        self.assertIn("# A/B 标题", result)
        self.assertIn("原文链接 https://example.com/a", result)
        self.assertIn("# 正文", result)

    def test_read_by_oid_and_block_params_are_ignored(self):
        server, _ = self._server([make_entry("1794880590020", "甲", markdown="正文内容")])
        result = server.siyuan_read({
            "document_id": "1794880590020",
            "include_block_ids": True,
            "block_start": 2,
            "block_limit": 1,
            "token_budget": 1000,
        })
        self.assertIn("正文内容", result)
        self.assertNotIn("[1 id=", result)

    def test_read_reports_ambiguous_title(self):
        entries = [make_entry("1", "同名"), make_entry("2", "同名")]
        server, _ = self._server(entries)
        with self.assertRaisesRegex(ValueError, "同名条目"):
            server.siyuan_read({"document_path": "/收集箱/同名"})

    def test_read_reports_missing_entry(self):
        server, _ = self._server([make_entry("1", "甲")])
        with self.assertRaisesRegex(ValueError, "没有匹配的条目"):
            server.siyuan_read({"document_path": "/收集箱/乙"})

    def test_edit_is_rejected_with_fixed_message(self):
        server, _ = self._server([make_entry("1", "甲")])
        with self.assertRaisesRegex(ValueError, "这是收集箱文档，只读"):
            server.siyuan_edit({
                "document_path": "/收集箱/甲",
                "action": "append",
                "markdown": "x",
                "confirmed": True,
            })

    def test_create_into_inbox_is_rejected(self):
        server, _ = self._server([make_entry("1", "甲")])
        with self.assertRaisesRegex(ValueError, "不能写入"):
            server.siyuan_create({"path": "/收集箱/新文档", "title": "新文档", "markdown": "x", "confirmed": True})

    def test_search_section_matches_title_only(self):
        entries = [make_entry("1794880590020", "朋友圈反着发"), make_entry("1792243354103", "无关标题")]
        server, _ = self._server(entries)
        section = server._inbox_search_section("朋友圈", "query", 20, None)
        self.assertIn("## 收集箱（1 条命中）", section)
        self.assertIn("`1794880590020`", section)
        body_only = server._inbox_search_section("正文里才有的词", "query", 20, None)
        self.assertEqual("", body_only)

    def test_search_section_skips_sql_and_notebook_filter(self):
        server, _ = self._server([make_entry("1", "甲")])
        self.assertEqual("", server._inbox_search_section("甲", "sql", 20, None))
        self.assertEqual("", server._inbox_search_section("甲", "query", 20, ["nb1"]))

    def test_start_overview_row_shows_entry_count(self):
        server, _ = self._server([make_entry("1", "甲", created="2026-11-17 09:56"), make_entry("2", "乙")])
        row = server._inbox_overview_row()
        self.assertEqual("| 收集箱 | - | 2 | - | - | 2026-11-17 09:56 |", row)
        overview = "| 笔记本 | ID | 文档数 |\n|---|---|---|\n| Main | `nb1` | 1 |"
        merged = mcp_server._append_overview_row(overview, row)
        self.assertIn("| Main | `nb1` | 1 |\n| 收集箱 | - | 2 |", merged)

    def test_move_on_legacy_version_uses_create_doc_with_md(self):
        server, client = self._server([make_entry("1794880590020", "A/B 标题", markdown="# 正文")], version="3.8.8")
        result = server.siyuan_doc_manage({
            "action": "move",
            "document_path": "/收集箱/A/B 标题",
            "target_parent": "/Main",
            "confirmed": True,
        })
        self.assertEqual(1, len(client.created_with_md))
        notebook_id, path, markdown = client.created_with_md[0]
        self.assertEqual("nb1", notebook_id)
        self.assertEqual("/A／B 标题", path)
        self.assertEqual("# 正文", markdown)
        self.assertEqual(["1794880590020"], client.removed)
        self.assertIn("收集箱条目已移出", result)
        self.assertEqual(1, len(client.snapshots))

    def test_move_on_new_version_uses_create_doc_by_parent(self):
        server, client = self._server([make_entry("1794880590020", "标题")], version="3.8.10")
        server.siyuan_doc_manage({
            "action": "move",
            "document_path": "/收集箱/标题",
            "target_parent": "/Main",
            "confirmed": True,
        })
        self.assertEqual(1, len(client.created_by_parent))
        notebook_id, parent_id, doc_id, title, _ = client.created_by_parent[0]
        self.assertEqual("nb1", notebook_id)
        self.assertIsNone(parent_id)
        self.assertEqual("标题", title)
        self.assertRegex(doc_id, r"^\d{14}-[0-9a-z]{7}$")

    def test_move_reports_partial_when_remove_fails(self):
        server, client = self._server([make_entry("1794880590020", "标题")], version="3.8.10")

        def boom(_ids):
            raise mcp_server.SiYuanApiError("cloud down")

        client.remove_shorthands = boom  # type: ignore[assignment]
        result = server.siyuan_doc_manage({
            "action": "move",
            "document_path": "/收集箱/标题",
            "target_parent": "/Main",
            "confirmed": True,
        })
        self.assertIn("文档已建立，但收集箱条目未删除", result)
        self.assertIn("cloud down", result)

    def test_delete_removes_cloud_entry(self):
        server, client = self._server([make_entry("1794880590020", "标题")])
        result = server.siyuan_doc_manage({
            "action": "delete",
            "document_path": "/收集箱/标题",
            "confirmed": True,
        })
        self.assertEqual(["1794880590020"], client.removed)
        self.assertIn("无法通过思源工作空间快照恢复", result)

    def test_doc_manage_other_actions_are_rejected(self):
        server, _ = self._server([make_entry("1", "甲")])
        with self.assertRaisesRegex(ValueError, "只支持 move 与 delete"):
            server.siyuan_doc_manage({
                "action": "rename",
                "document_path": "/收集箱/甲",
                "new_title": "乙",
                "confirmed": True,
            })

    def test_move_requires_confirmed(self):
        server, client = self._server([make_entry("1", "甲")])
        with self.assertRaisesRegex(ValueError, "confirmed=true"):
            server.siyuan_doc_manage({"action": "move", "document_path": "/收集箱/甲", "target_parent": "/Main"})
        self.assertEqual([], client.removed)


if __name__ == "__main__":
    unittest.main()
