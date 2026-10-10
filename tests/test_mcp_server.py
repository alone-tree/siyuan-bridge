from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from source_code import mcp_server
from source_code.client import SiYuanApiError, SiYuanConnectionError, SiYuanTimeoutError
from source_code.config import Profile, load_config
from source_code.ignore import PrivacyRules, write_privacy_rules_cache


class FakeSearchClient:
    def __init__(self, blocks: list[dict[str, Any]], *, closed: bool = False, sql_rows: list[dict[str, Any]] | None = None):
        self.blocks = blocks
        self.sql_rows = sql_rows
        self.sql_info: dict[str, Any] = {"limit": 64, "truncated": False}
        self.sql_statements: list[str] = []
        self.kramdown_reads: list[str] = []
        self.closed = closed
        self.base_url = "http://127.0.0.1:6806"
        self.opened: list[str] = []
        self.closed_again: list[str] = []
        self.seen_payloads: list[dict[str, Any]] = []
        self._snapshots: list[dict[str, Any]] = []
        self._docs: dict[str, str] = {}  # doc_id -> markdown
        self._blocks: dict[str, dict[str, Any]] = {}  # block_id -> block info
        self._refs: list[dict[str, Any]] = []
        self._push_msgs: list[str] = []
        self._updated_blocks: list[tuple[str, str]] = []
        self._appended_blocks: list[tuple[str, str]] = []
        self._inserted_after: list[tuple[str, str]] = []
        self._inserted_before: list[tuple[str, str]] = []
        self._inserted_assets: list[tuple[str, list[str], bool]] = []
        self._deleted_blocks: list[str] = []
        self._created_docs: list[tuple[str, str, str]] = []
        self._created_notebooks: list[dict[str, Any]] = []
        self._renamed_docs: list[tuple[str, str]] = []
        self._removed_docs: list[str] = []
        self._moved_docs: list[tuple[list[str], str]] = []
        self._duplicated_docs: list[str] = []
        self._set_attrs_calls: list[tuple[str, dict[str, str]]] = []
        self._hpaths: dict[str, str] = {"doc1": "/Projects/Doc One", "doc2": "/Projects/Hidden", "doc3": "/Projects/Doc One/Child"}
        self._sync_performed = False
        self._sync_timeout = None
        self._sync_info = {"stat": "Synced", "synced": 20260614010101}
        self.force_empty_child_reads = 0
        self._empty_child_reads = 0

    def version(self):
        return "3.0.0"

    def list_notebooks(self):
        return [{"id": "nb1", "name": "Main", "closed": self.closed}, *self._created_notebooks]

    def create_notebook(self, name):
        notebook = {"id": f"nb-{name}", "name": name, "closed": False}
        self._created_notebooks.append(notebook)
        return notebook

    def open_notebook(self, notebook_id):
        self.opened.append(notebook_id)
        self.closed = False

    def close_notebook(self, notebook_id):
        self.closed_again.append(notebook_id)
        self.closed = True

    def query_sql_with_info(self, stmt):
        self.sql_statements.append(stmt)
        return {"code": 0, "data": list(self.sql_rows or []), **self.sql_info}

    def query_sql(self, _stmt):
        self.sql_statements.append(str(_stmt))
        stmt = str(_stmt).casefold() if _stmt else ""
        if "from blocks" in stmt and "where id in" in stmt:
            import re
            match = re.search(r"where\s+id\s+in\s*\((.*?)\)", str(_stmt), re.IGNORECASE)
            wanted = set(re.findall(r"'((?:''|[^'])*)'", match.group(1))) if match else set()
            wanted = {value.replace("''", "'") for value in wanted}
            metadata = []
            for doc_id, hpath in self._hpaths.items():
                if doc_id in wanted:
                    metadata.append({"id": doc_id, "root_id": doc_id, "box": "nb1",
                                     "hpath": hpath, "path": f"/{doc_id}.sy", "type": "d"})
            for root_id, blocks in self._blocks.items():
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if block.get("id") in wanted:
                        doc_id = str(block.get("root_id", root_id))
                        metadata.append({"root_id": doc_id, "box": "nb1",
                                         "hpath": self._hpaths.get(doc_id, ""),
                                         "path": f"/{doc_id}.sy", **block})
            return metadata
        if "from blocks" in stmt and "where id" in stmt:
            import re
            match = re.search(r"where\s+id\s*=\s*'([^']+)'", stmt)
            if match:
                block_id = match.group(1)
                for root_id, blocks in self._blocks.items():
                    if not isinstance(blocks, list):
                        continue
                    for block in blocks:
                        if str(block.get("id", "")).casefold() == block_id:
                            return [{
                                "id": block.get("id", ""),
                                "root_id": block.get("root_id", root_id),
                                "type": block.get("type", ""),
                            }]
            return []
        if "from blocks" in stmt and ("type='d'" in stmt or "type = 'd'" in stmt):
            return [
                {
                    "id": doc_id,
                    "box": "nb1",
                    "hpath": hpath,
                    "path": f"/{doc_id}.sy",
                    "name": hpath.strip("/").split("/")[-1],
                    "type": "d",
                    "updated": "20260501010101",
                }
                for doc_id, hpath in self._hpaths.items()
            ]
        if "from blocks" in stmt and "root_id" in stmt:
            # Extract root_id from WHERE clause for filtering
            import re
            m = re.search(r"root_id\s*=\s*'([^']+)'", stmt)
            if m:
                doc_id = m.group(1)
                return self._blocks.get(doc_id, [])
            for blocks in self._blocks.values():
                if isinstance(blocks, list):
                    return blocks
            return []
        if self.sql_rows is not None:
            return self.sql_rows
        return [{"exists": 1}]

    def search_full_text(self, **payload):
        self.seen_payloads.append(payload)
        return {"blocks": self.blocks}

    # Write methods
    def create_snapshot(self, memo):
        snap = {"memo": memo, "created": "20260503000000"}
        self._snapshots.append(snap)
        return snap

    def perform_sync(self, *, timeout=10.0):
        self._sync_performed = True
        self._sync_timeout = timeout
        return {}

    def get_sync_info(self):
        return self._sync_info

    def create_doc_with_md(self, notebook, path, markdown):
        self._created_docs.append((notebook, path, markdown))
        doc_id = f"new-doc-{len(self._docs)}"
        self._docs[doc_id] = markdown
        self._hpaths[doc_id] = path
        self._blocks[doc_id] = self._new_blocks(doc_id, markdown)
        return {"id": doc_id}

    def rename_doc_by_id(self, doc_id, title):
        self._renamed_docs.append((doc_id, title))
        old = self._hpaths.get(doc_id, "")
        parent = "/" + "/".join(old.strip("/").split("/")[:-1]) if "/" in old.strip("/") else ""
        self._hpaths[doc_id] = mcp_server.normalize_display_path(f"{parent}/{title}")
        return {}

    def remove_doc_by_id(self, doc_id):
        self._removed_docs.append(doc_id)
        self._hpaths.pop(doc_id, None)
        return {}

    def move_docs_by_id(self, doc_ids, target_id):
        self._moved_docs.append((doc_ids, target_id))
        for doc_id in doc_ids:
            title = self._hpaths.get(doc_id, f"/{doc_id}").strip("/").split("/")[-1]
            self._hpaths[doc_id] = f"/{title}"
        return {}

    def duplicate_doc(self, doc_id):
        self._duplicated_docs.append(doc_id)
        new_id = f"duplicated-{len(self._duplicated_docs)}"
        self._docs[new_id] = self._docs.get(doc_id, "")
        self._hpaths[new_id] = self._hpaths.get(doc_id, f"/{doc_id}") + " (Duplicated)"
        return {"id": new_id}

    def get_hpath_by_id(self, block_id):
        hpath = self._hpaths.get(block_id, "")
        if not hpath:
            raise RuntimeError("not found")
        return hpath

    def update_block(self, block_id, markdown):
        self._updated_blocks.append((block_id, markdown))
        for block_list in self._blocks.values():
            if not isinstance(block_list, list):
                continue
            for block in block_list:
                if str(block.get("id", "")) == block_id:
                    block["markdown"] = markdown
                    block["type"] = self._block_type(markdown)
                    return

    def append_block(self, parent_id, markdown):
        self._appended_blocks.append((parent_id, markdown))
        blocks = self._blocks.setdefault(parent_id, [])
        if isinstance(blocks, list):
            blocks.extend(self._new_blocks(parent_id, markdown))

    def insert_block_after(self, previous_id, markdown):
        self._inserted_after.append((previous_id, markdown))
        self._insert_near(previous_id, markdown, after=True)

    def insert_block_before(self, next_id, markdown):
        self._inserted_before.append((next_id, markdown))
        self._insert_near(next_id, markdown, after=False)

    def insert_local_assets(self, document_id, asset_paths, *, is_upload=True):
        self._inserted_assets.append((document_id, list(asset_paths), is_upload))
        result = {}
        for raw_path in asset_paths:
            path = Path(raw_path)
            result[path.name] = (
                f"file://{raw_path}"
                if path.is_dir()
                else f"assets/{path.name}"
            )
        return result

    def delete_block(self, block_id):
        self._deleted_blocks.append(block_id)
        for block_list in self._blocks.values():
            if isinstance(block_list, list):
                block_list[:] = [block for block in block_list if str(block.get("id", "")) != block_id]

    def set_block_attrs(self, block_id, attrs):
        self._set_attrs_calls.append((block_id, dict(attrs)))

    def get_attribute_view(self, av_id):
        return {}

    def push_msg(self, msg, timeout=7000):
        self._push_msgs.append(msg)

    def export_markdown(self, block_id):
        if block_id in self._docs:
            return self._docs[block_id]
        return ""

    def get_block_kramdown(self, block_id):
        self.kramdown_reads.append(block_id)
        for blocks in self._blocks.values():
            if isinstance(blocks, list):
                for block in blocks:
                    if block.get("id") == block_id:
                        return str(block.get("markdown", ""))
        raise SiYuanApiError("block not found")

    def get_asset(self, asset_path):
        return b""

    def list_document_blocks(self, doc_id):
        stmt = f"SELECT id, parent_id, root_id, type, subtype, markdown, content, sort FROM blocks WHERE root_id = '{doc_id}' AND type != 'd' ORDER BY sort"
        return self.query_sql(stmt)

    def list_block_references(self, block_ids):
        wanted = {str(block_id) for block_id in block_ids}
        return [
            dict(row)
            for row in self._refs
            if str(row.get("def_block_id", "")) in wanted
        ]

    def list_forward_block_references(self, block_ids):
        wanted = set(block_ids)
        targets = {
            str(block["id"]): {"root_id": doc_id, **block}
            for doc_id, blocks in self._blocks.items()
            if isinstance(blocks, list)
            for block in blocks
        }
        targets.update({
            doc_id: {"root_id": doc_id, "markdown": path, "content": path, "type": "d"}
            for doc_id, path in self._hpaths.items()
        })
        rows = []
        for row in self._refs:
            if row.get("block_id") not in wanted:
                continue
            target = targets.get(row.get("def_block_id"), {})
            rows.append({
                **row,
                "target_root_id": target.get("root_id", ""),
                "target_markdown": target.get("markdown"),
                "target_content": target.get("content"),
            })
        return rows

    def get_child_blocks(self, block_id):
        if self.force_empty_child_reads > 0:
            self.force_empty_child_reads -= 1
            self._empty_child_reads += 1
            return []
        blocks = self._blocks.get(block_id)
        if isinstance(blocks, list):
            return blocks
        children = []
        for block_list in self._blocks.values():
            if isinstance(block_list, list):
                children.extend(block for block in block_list if str(block.get("parent_id", "")) == block_id)
        children.sort(key=lambda block: int(block.get("sort", 0)))
        return children

    def _block_type(self, markdown):
        text = str(markdown or "").strip()
        if text.startswith("|") and "\n|" in text:
            return "t"
        if text.startswith("#"):
            return "h"
        if text.startswith("```"):
            return "c"
        return "p"

    def _new_blocks(self, parent_id, markdown):
        parts = [part.strip() for part in str(markdown).split("\n\n") if part.strip()]
        blocks = self._blocks.get(parent_id)
        existing = blocks if isinstance(blocks, list) else []
        next_sort = max((int(block.get("sort", 0)) for block in existing), default=0) + 1
        created = []
        for offset, part in enumerate(parts):
            created.append({
                "id": f"new{len(self._appended_blocks) + len(self._inserted_after) + len(self._inserted_before)}-{offset}",
                "type": self._block_type(part),
                "markdown": part,
                "parent_id": parent_id,
                "sort": next_sort + offset,
            })
        return created

    def _insert_near(self, anchor_id, markdown, *, after):
        for doc_id, block_list in self._blocks.items():
            if not isinstance(block_list, list):
                continue
            for index, block in enumerate(block_list):
                if str(block.get("id", "")) == anchor_id:
                    insert_at = index + 1 if after else index
                    block_list[insert_at:insert_at] = self._new_blocks(str(doc_id), markdown)
                    return


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / ".test_tmp" / "mcp_find"
        shutil.rmtree(self.root, ignore_errors=True)
        base = self.root / "knowledge_base"
        base.mkdir(parents=True, exist_ok=True)
        (base / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "Main"}], ensure_ascii=False),
            encoding="utf-8",
        )
        write_privacy_rules_cache(self.root, PrivacyRules(ignore=[], allow=[]))
        docs = [
            {
                "id": "doc1",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Projects/Doc One",
                "title": "Doc One",
                "path": "/doc1.sy",
                "tags": [],
                "word_count": 123,
                "block_count": 4,
                "updated": "20260501010101",
            },
            {
                "id": "doc2",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Projects/Hidden",
                "title": "Hidden",
                "path": "/doc2.sy",
                "tags": [],
                "word_count": 50,
                "block_count": 2,
                "updated": "20260501010102",
            },
            {
                "id": "doc3",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Projects/Doc One/Child",
                "title": "Child",
                "path": "/doc3.sy",
                "tags": [],
                "word_count": 30,
                "block_count": 1,
                "updated": "20260501010103",
            },
        ]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs),
            encoding="utf-8",
        )
    def run_operate(self, client: FakeSearchClient, args: dict[str, Any]) -> str:
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        return server.siyuan_operate(args)

    def test_list_without_args_lists_notebooks(self):
        server = mcp_server.McpServer(self.root)
        result = server.siyuan_list({})
        self.assertIn("# 可见笔记本", result)
        self.assertIn("| notebook | notebook_id | 权限 |", result)
        self.assertIn("| Main | `nb1` | read_write |", result)

    def test_list_root_path_lists_notebooks(self):
        server = mcp_server.McpServer(self.root)
        result = server.siyuan_list({"path": "/"})
        self.assertIn("# 可见笔记本", result)
        self.assertIn("| Main | `nb1` | read_write |", result)

    def test_tool_call_requires_start_before_local_list(self):
        server = mcp_server.McpServer(self.root)
        response = server.call_tool(1, "siyuan_list", {})

        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        self.assertIn("思源桥尚未初始化", text)
        self.assertIn("请先调用 siyuan_start", text)

    def test_tool_call_reuses_started_client_without_profile_detection(self):
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = FakeSearchClient([])

        with mock.patch.object(
            mcp_server,
            "detect_active_profile",
            side_effect=AssertionError("不应重新探测 profile"),
        ):
            response = server.call_tool(1, "siyuan_list", {})

        self.assertFalse(response["result"].get("isError", False))
        self.assertIn("# 可见笔记本", response["result"]["content"][0]["text"])

    def test_failed_start_clears_previous_connection_and_preserves_reason(self):
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="old", token="old")
        server._active_client = FakeSearchClient([])

        with mock.patch.object(
            mcp_server,
            "detect_active_profile",
            side_effect=SiYuanConnectionError("API 可达，但所有 Token 都不可用"),
        ):
            response = server.call_tool(1, "siyuan_start", {})

        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        self.assertIn("思源桥启动失败", text)
        self.assertIn("所有 Token 都不可用", text)
        self.assertIsNone(server._active_profile)
        self.assertIsNone(server._active_client)

    def test_successful_start_caches_profile_and_client_after_initialization(self):
        server = mcp_server.McpServer(self.root)
        profile = Profile(name="current", token="token")
        client = mock.Mock()
        client.version.return_value = "3.3.0"
        state = mock.Mock(
            notebook_id="system-nb",
            privacy_rules_doc_ids=("privacy-doc",),
            missing_document_keys=(),
            privacy_rules=PrivacyRules(ignore=[], allow=[]),
            mcp_usage_guide_markdown="Guide",
            ai_guide_markdown="Preferences",
            workspace_index_updated="",
            workspace_index_is_placeholder=True,
            workspace_index_markdown="",
        )

        with (
            mock.patch.object(mcp_server, "detect_active_profile", return_value=(profile, client)),
            mock.patch.object(mcp_server, "load_agent_notebook", return_value=state),
            mock.patch.object(mcp_server, "refresh_index"),
            mock.patch.object(mcp_server, "build_notebook_overview", return_value="# 概览\n\n内容"),
        ):
            result = server.siyuan_start({})

        self.assertIn("# 思源桥启动包", result)
        self.assertIs(server._active_profile, profile)
        self.assertIs(server._active_client, client)

    def test_missing_privacy_rules_blocks_start_and_requests_plugin_reload(self):
        server = mcp_server.McpServer(self.root)
        profile = Profile(name="current", token="token")
        client = mock.Mock()
        error = mcp_server.PrivacyRulesUnavailableError(
            "隐私规则文档缺失。请禁用并重新启用思源桥插件后再次启动。"
        )

        with (
            mock.patch.object(mcp_server, "detect_active_profile", return_value=(profile, client)),
            mock.patch.object(mcp_server, "load_agent_notebook", side_effect=error),
        ):
            response = server.call_tool(1, "siyuan_start", {})

        self.assertTrue(response["result"]["isError"])
        self.assertIn("禁用并重新启用", response["result"]["content"][0]["text"])
        client.push_err_msg.assert_called_once()
        self.assertIsNone(server._active_profile)
        self.assertIsNone(server._active_client)

    def test_auth_error_invalidates_started_connection(self):
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = FakeSearchClient([])

        def fail_auth(_args):
            raise SiYuanApiError("Unauthorized", status=401)

        server.siyuan_find = fail_auth
        response = server.call_tool(1, "siyuan_find", {"query": "test"})

        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        self.assertIn("API Token 已失效", text)
        self.assertIn("重新调用 siyuan_start", text)
        self.assertIsNone(server._active_profile)
        self.assertIsNone(server._active_client)

    def test_timeout_does_not_invalidate_connection(self):
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = FakeSearchClient([])

        def fail_timeout(_args):
            raise SiYuanTimeoutError("Request timed out")

        server.siyuan_find = fail_timeout
        response = server.call_tool(1, "siyuan_find", {"query": "test"})

        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        self.assertIn("超时", text)
        self.assertIn("请稍后重试", text)
        self.assertNotIn("请重新调用 siyuan_start", text)
        self.assertIsNotNone(server._active_profile)
        self.assertIsNotNone(server._active_client)

    def test_start_timeout_clears_connection(self):
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = FakeSearchClient([])

        def fail_timeout(_args):
            raise SiYuanTimeoutError("Request timed out")

        server.siyuan_start = fail_timeout
        response = server.call_tool(1, "siyuan_start", {})

        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        self.assertIn("启动失败", text)
        self.assertIn("超时", text)
        self.assertIsNone(server._active_profile)
        self.assertIsNone(server._active_client)

    def test_tool_specs_expose_operate_not_refresh_index(self):
        specs = mcp_server.tool_specs()
        names = [tool["name"] for tool in specs]
        self.assertIn("siyuan_operate", names)
        self.assertNotIn("siyuan_refresh_index", names)
        start = next(tool for tool in specs if tool["name"] == "siyuan_start")
        self.assertIn("never creates", start["description"])
        self.assertIn("Privacy Rules", start["description"])
        operate = next(tool for tool in specs if tool["name"] == "siyuan_operate")
        self.assertIn("does not create or edit documents", operate["description"])
        self.assertNotIn("markdown_file", operate["description"])
        properties = operate["inputSchema"]["properties"]
        self.assertEqual(properties["action"]["enum"], [
            "refresh", "sync", "check_forward_references", "check_backward_references",
        ])
        self.assertNotIn("check_references", json.dumps(operate))
        self.assertNotIn("direction", properties)
        self.assertIn("grouped by target document", properties["action"]["description"])
        self.assertIn("grouped by source document", properties["action"]["description"])
        self.assertIn("within the same document", properties["action"]["description"])
        self.assertIn("without cleaning ai_workspace", properties["action"]["description"])
        self.assertIn("document", properties)
        self.assertIn("document_id", properties)
        self.assertEqual(properties["limit"]["default"], 10)
        self.assertEqual(properties["limit"]["anyOf"][0]["minimum"], 1)
        self.assertEqual(properties["limit"]["anyOf"][1]["enum"], ["none"])

        doc_manage = next(tool for tool in specs if tool["name"] == "siyuan_doc_manage")
        self.assertIn("document-tree level", doc_manage["description"])
        self.assertIn("workspace snapshot", doc_manage["description"])
        doc_manage_properties = doc_manage["inputSchema"]["properties"]
        self.assertIn("create_notebook", doc_manage_properties["action"]["enum"])
        self.assertIn("notebook_name", doc_manage_properties)
        self.assertIn("does not create a document", doc_manage_properties["action"]["description"])
        self.assertIn("not a notebook", doc_manage_properties["action"]["description"])
        self.assertIn("not children", doc_manage_properties["action"]["description"])

    def test_find_tool_spec_exposes_query_as_default_without_keyword_mode(self):
        spec = next(tool for tool in mcp_server.tool_specs() if tool["name"] == "siyuan_find")
        properties = spec["inputSchema"]["properties"]
        mode = properties["mode"]
        self.assertEqual(mode["default"], "query")
        self.assertEqual(mode["enum"], ["query", "regex", "sql"])
        self.assertIn("query", properties)
        self.assertNotIn("keyword", properties)
        self.assertEqual(spec["inputSchema"]["required"], ["query"])
        self.assertIn("whitespace means AND", spec["description"])
        self.assertIn("GPU AND optical", properties["query"]["description"])
        self.assertIn("GPU OR optical OR NVLink", properties["query"]["description"])
        self.assertIn("Scale-out", properties["query"]["description"])

    def test_edit_tool_spec_exposes_insert_assets_name_and_title_semantics(self):
        spec = next(tool for tool in mcp_server.tool_specs() if tool["name"] == "siyuan_edit")
        properties = spec["inputSchema"]["properties"]
        self.assertIn("insert_assets", properties["action"]["enum"])
        self.assertEqual(properties["upload_large_files"]["default"], False)
        asset_properties = properties["assets"]["items"]["properties"]
        self.assertIn("Visible body name", asset_properties["name"]["description"])
        self.assertIn("caption below the image", asset_properties["title"]["description"])
        self.assertEqual(properties["assets"]["items"]["required"], ["local_path"])
        self.assertNotIn("asset_paths", spec["description"])
        self.assertIn("asset_paths", properties["assets"]["description"])
        self.assertIn("Choose the edit method with action", spec["description"])
        self.assertIn("pass new body text with markdown", spec["description"])
        self.assertIn("workspace snapshot", spec["description"])
        self.assertIn("Do not write a local .md file first", properties["markdown"]["description"])
        self.assertIn("Do not create a temporary .md just to insert it", properties["markdown_file"]["description"])
        action_enum = properties["action"]["enum"]
        self.assertEqual(action_enum[0], "default_block_replace")
        self.assertIn("single_block_replace", action_enum)
        self.assertNotIn("multi_block_replace", action_enum)
        self.assertNotIn("multi_block_replace", spec["description"])
        self.assertNotIn("multi_block_replace", properties["action"]["description"])
        self.assertNotIn("multi_block_replace", properties["markdown"]["description"])
        self.assertIn("insert_after", properties["action"]["description"])
        self.assertIn("append", properties["action"]["description"])
        self.assertIn("delete", properties["action"]["description"])
        self.assertIn("table_edit", properties["action"]["description"])
        self.assertIn("without preserving formatting", properties["action"]["description"])
        self.assertIn("referenced by other blocks", properties["action"]["description"])
        self.assertIn("those replacements must use default_block_replace", properties["markdown"]["description"])

    def test_create_tool_spec_prefers_markdown_over_markdown_file(self):
        spec = next(tool for tool in mcp_server.tool_specs() if tool["name"] == "siyuan_create")
        properties = spec["inputSchema"]["properties"]
        self.assertIn("Pass new body text with markdown", spec["description"])
        self.assertNotIn("if_exists", spec["description"])
        self.assertIn("Do not write a local .md file first", properties["markdown"]["description"])
        self.assertIn("Do not create a temporary .md just to insert it", properties["markdown_file"]["description"])

    def test_insert_assets_rejects_unsupported_top_level_parameter_with_example(self):
        server = mcp_server.McpServer(self.root)
        with self.assertRaises(ValueError) as ctx:
            server.siyuan_edit({
                "action": "insert_assets",
                "asset_paths": [r"C:\\image.png"],
                "confirmed": True,
            })
        message = str(ctx.exception)
        self.assertIn("asset_paths 参数无效", message)
        self.assertIn('"assets":[{"local_path"', message)
        self.assertIn("不要使用 asset_paths 或 path", message)

    def test_insert_assets_rejects_unsupported_item_parameter_with_example(self):
        with self.assertRaises(ValueError) as ctx:
            mcp_server.preflight_asset_items([{"path": r"C:\\image.png"}])
        message = str(ctx.exception)
        self.assertIn("path 参数无效", message)
        self.assertIn('"assets":[{"local_path"', message)

    def test_insert_assets_rejects_string_item_with_example(self):
        with self.assertRaises(ValueError) as ctx:
            mcp_server.preflight_asset_items([r"C:\\image.png"])
        message = str(ctx.exception)
        self.assertIn("assets[1] 参数无效", message)
        self.assertIn('"assets":[{"local_path"', message)

    def test_render_asset_markdown_escapes_labels_titles_and_spaced_destinations(self):
        item = mcp_server.AssetInsertionItem(
            local_path=r"D:\files\a.png",
            basename="a.png",
            kind="image",
            name=r"A [chart]\name",
            title='Quarter "one"',
            size_bytes=10,
        )
        rendered = mcp_server.render_asset_markdown(
            item,
            r"file://D:\folder with space\a.png",
        )
        self.assertEqual(
            rendered,
            '![A \\[chart\\]\\\\name](<file://D:\\folder with space\\a.png> "Quarter \\"one\\"")',
        )

    def test_operate_sync_calls_default_siyuan_sync(self):
        client = FakeSearchClient([])
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        result = server.siyuan_operate({"action": "sync"})

        self.assertTrue(client._sync_performed)
        self.assertEqual(client._sync_timeout, 10.0)
        self.assertIn("# 同步已完成", result)
        self.assertIn("状态：Synced", result)

    def test_operate_sync_accepts_custom_timeout(self):
        client = FakeSearchClient([])
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        server.siyuan_operate({"action": "sync", "timeout_seconds": 30})

        self.assertEqual(client._sync_timeout, 30.0)

    def test_operate_sync_timeout_has_specific_error_code(self):
        class TimeoutSyncClient(FakeSearchClient):
            def perform_sync(self, *, timeout=10.0):
                raise SiYuanTimeoutError("Request timed out")

        client = TimeoutSyncClient([])
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        with self.assertRaises(ValueError) as ctx:
            server.siyuan_operate({"action": "sync"})

        self.assertEqual(getattr(ctx.exception, "error_code", None), "api:sync_timeout")
        self.assertIn("同步超过 10 秒", str(ctx.exception))

    def test_operate_sync_connection_error_has_specific_error_code(self):
        class BrokenSyncClient(FakeSearchClient):
            def perform_sync(self, *, timeout=10.0):
                raise SiYuanConnectionError("network unreachable")

        client = BrokenSyncClient([])
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        with self.assertRaises(ValueError) as ctx:
            server.siyuan_operate({"action": "sync"})

        self.assertEqual(getattr(ctx.exception, "error_code", None), "api:sync_connection")
        self.assertIn("同步连接失败", str(ctx.exception))

    def test_operate_requires_known_action(self):
        server = mcp_server.McpServer(self.root)
        with self.assertRaises(ValueError):
            server.siyuan_operate({"action": "bad"})

    def test_operate_check_backward_references_groups_sources_and_summarizes_children(self):
        client = FakeSearchClient([])
        client._blocks["doc1"] = [{
            "id": "target-block",
            "root_id": "doc1",
            "parent_id": "doc1",
            "type": "p",
            "markdown": "Target",
            "content": "Target",
            "sort": 1,
        }]
        client._blocks["doc3"] = [{
            "id": "child-block",
            "root_id": "doc3",
            "parent_id": "doc3",
            "type": "i",
            "markdown": "Nested child target",
            "content": "Nested child target",
            "sort": 1,
        }]
        long_markdown = "A" * 2100
        client._refs = [
            {
                "def_block_id": "doc1",
                "block_id": "source-block-1",
                "root_id": "doc2",
                "type": "textmark",
                "content": long_markdown,
                "markdown": long_markdown,
            },
            {
                "def_block_id": "target-block",
                "block_id": "source-block-1",
                "root_id": "doc2",
                "type": "block-link",
                "content": long_markdown,
                "markdown": long_markdown,
            },
            {
                "def_block_id": "target-block",
                "block_id": "source-block-2",
                "root_id": "doc2",
                "type": "textmark",
                "content": "Second source block",
                "markdown": "Second source block",
            },
            {
                "def_block_id": "child-block",
                "block_id": "source-block-3",
                "root_id": "doc2",
                "type": "textmark",
                "content": "Child reference",
                "markdown": "Child reference",
            },
        ]

        result = self.run_operate(
            client,
            {"action": "check_backward_references", "document": "/Main/Projects/Doc One"},
        )

        self.assertIn("本文档总共被引用 3 次。", result)
        self.assertIn("其所有子文档（不含本文档）总共被引用 1 次", result)
        self.assertIn("/Main/Projects/Doc One/Child（`doc3`）被引用 1 次", result)
        self.assertIn("/Main/Projects/Hidden（`doc2`）引用了 3 次", result)
        self.assertIn("引用1（本块包含 2 次引用）：", result)
        self.assertIn("引用2：", result)
        self.assertIn("内容超过 2000 字符，已截断", result)

    def test_operate_check_backward_references_aggregates_hidden_sources_and_targets(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[
                    {"scope": "document", "id": "doc2"},
                    {"scope": "document", "id": "doc3"},
                ],
                allow=[],
            ),
        )
        client = FakeSearchClient([])
        client._refs = [
            {
                "def_block_id": "doc1",
                "block_id": "hidden-source-1",
                "root_id": "doc2",
                "type": "textmark",
                "content": "Hidden source content",
                "markdown": "Hidden source content",
            },
            {
                "def_block_id": "doc3",
                "block_id": "hidden-source-2",
                "root_id": "doc2",
                "type": "block-link",
                "content": "Hidden child target content",
                "markdown": "Hidden child target content",
            },
        ]

        result = self.run_operate(
            client,
            {"action": "check_backward_references", "document_id": "doc1"},
        )

        self.assertIn("本文档总共被引用 1 次。", result)
        self.assertIn("其所有子文档（不含本文档）总共被引用 1 次", result)
        self.assertIn("无可展示的子文档。", result)
        self.assertIn("隐藏文档中引用了 1 次。", result)
        self.assertNotIn("/Main/Projects/Hidden", result)
        self.assertNotIn("/Main/Projects/Doc One/Child", result)
        self.assertNotIn("Hidden source content", result)

    def test_operate_check_backward_references_reports_zero_without_error(self):
        result = self.run_operate(
            FakeSearchClient([]),
            {"action": "check_backward_references", "document_id": "doc2"},
        )

        self.assertIn("本文档总共被引用 0 次。", result)
        self.assertNotIn("## 引用来源", result)

    def test_operate_check_backward_references_validates_limit_and_target_type(self):
        client = FakeSearchClient([])
        with self.assertRaises(ValueError) as limit_error:
            self.run_operate(
                client,
                {"action": "check_backward_references", "document_id": "doc1", "limit": 0},
            )
        self.assertEqual(getattr(limit_error.exception, "error_code", None), "validation:out_of_range")

        with self.assertRaises(ValueError) as notebook_error:
            self.run_operate(
                client,
                {"action": "check_backward_references", "document": "/Main"},
            )
        self.assertEqual(getattr(notebook_error.exception, "error_code", None), "validation:wrong_target_type")

        block_id = "20260729120000-abcdefg"
        client._blocks["doc1"] = [{
            "id": block_id,
            "root_id": "doc1",
            "type": "p",
            "markdown": "Body block",
        }]
        with self.assertRaises(ValueError) as block_error:
            self.run_operate(
                client,
                {"action": "check_backward_references", "document_id": block_id},
            )
        self.assertEqual(getattr(block_error.exception, "error_code", None), "validation:wrong_target_type")
        self.assertIn("指向文档内块", str(block_error.exception))

    def test_operate_rejects_old_reference_action_name(self):
        with self.assertRaises(ValueError) as ctx:
            self.run_operate(FakeSearchClient([]), {"action": "check_references", "document_id": "doc1"})
        self.assertEqual(getattr(ctx.exception, "error_code", None), "validation:invalid_enum")
        self.assertNotIn("check_references", str(ctx.exception))

    def test_operate_check_forward_references_groups_targets_and_counts_internal(self):
        client = FakeSearchClient([])
        long_markdown = "B" * 2100
        client._blocks["doc1"] = [
            {
                "id": "source-block",
                "root_id": "doc1",
                "parent_id": "doc1",
                "type": "p",
                "markdown": "Source text that must not be shown",
                "content": "Source text that must not be shown",
                "sort": 1,
            },
            {
                "id": "internal-target",
                "root_id": "doc1",
                "parent_id": "doc1",
                "type": "p",
                "markdown": "Internal target",
                "content": "Internal target",
                "sort": 2,
            },
        ]
        client._blocks["doc2"] = [
            {
                "id": "external-target",
                "root_id": "doc2",
                "parent_id": "doc2",
                "type": "p",
                "markdown": long_markdown,
                "content": long_markdown,
                "sort": 1,
            },
            {
                "id": "external-target-2",
                "root_id": "doc2",
                "parent_id": "doc2",
                "type": "p",
                "markdown": "Second target",
                "content": "Second target",
                "sort": 2,
            },
        ]
        client._blocks["doc3"] = [{
            "id": "child-source",
            "root_id": "doc3",
            "parent_id": "doc3",
            "type": "p",
            "markdown": "Child source",
            "content": "Child source",
            "sort": 1,
        }]
        client._refs = [
            {"def_block_id": "internal-target", "block_id": "source-block", "root_id": "doc1"},
            {"def_block_id": "external-target", "block_id": "source-block", "root_id": "doc1"},
            {"def_block_id": "external-target-2", "block_id": "source-block", "root_id": "doc1"},
            {"def_block_id": "external-target", "block_id": "child-source", "root_id": "doc3"},
            {"def_block_id": "doc1", "block_id": "outside-source", "root_id": "doc2"},
        ]

        result = self.run_operate(
            client,
            {"action": "check_forward_references", "document": "/Main/Projects/Doc One"},
        )

        self.assertIn("# 文档正向引用检测", result)
        self.assertIn("本文档总共引用 3 次。", result)
        self.assertIn("其所有子文档（不含本文档）总共引用 1 次", result)
        self.assertIn("/Main/Projects/Doc One/Child（`doc3`）引用 1 次", result)
        self.assertLess(
            result.index("/Main/Projects/Hidden（`doc2`）被引用了 2 次"),
            result.index("/Main/Projects/Doc One（`doc1`）被引用了 1 次"),
        )
        self.assertIn("Internal target", result)
        self.assertIn("Second target", result)
        self.assertIn("内容超过 2000 字符，已截断", result)
        self.assertNotIn("Source text that must not be shown", result)
        self.assertNotIn("outside-source", result)

    def test_operate_check_forward_references_hides_target_documents(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(ignore=[{"scope": "document", "id": "doc2"}], allow=[]),
        )
        client = FakeSearchClient([])
        client._blocks["doc1"] = [{
            "id": "source-block",
            "root_id": "doc1",
            "parent_id": "doc1",
            "type": "p",
            "markdown": "Source",
            "content": "Source",
            "sort": 1,
        }]
        client._blocks["doc2"] = [{
            "id": "hidden-target",
            "root_id": "doc2",
            "parent_id": "doc2",
            "type": "p",
            "markdown": "Hidden target content",
            "content": "Hidden target content",
            "sort": 1,
        }]
        client._refs = [
            {"def_block_id": "hidden-target", "block_id": "source-block", "root_id": "doc1"},
            {"def_block_id": "missing-target", "block_id": "doc1", "root_id": "doc1"},
        ]

        result = self.run_operate(
            client,
            {"action": "check_forward_references", "document_id": "doc1"},
        )

        self.assertIn("本文档总共引用 2 次。", result)
        self.assertIn("引用了隐藏或无法定位文档中的块 2 次。", result)
        self.assertNotIn("/Main/Projects/Hidden", result)
        self.assertNotIn("hidden-target", result)
        self.assertNotIn("Hidden target content", result)
        self.assertNotIn("missing-target", result)
        self.assertNotIn("## 引用目标", result)

    def test_list_path_returns_direct_children_with_full_paths(self):
        server = mcp_server.McpServer(self.root)
        result = server.siyuan_list({"path": "/Main/Projects"})
        self.assertIn("| document | document_id | 权限 | 字数 | 块数 | 更新 | 子文档 |", result)
        self.assertIn("| /Main/Projects/Doc One | `doc1` | read_write | 123 | 4 | 2026-05-01 | 1 |", result)
        self.assertIn("| /Main/Projects/Hidden | `doc2` | read_write | 50 | 2 | 2026-05-01 | 0 |", result)
        self.assertNotIn("/Main/Projects/Doc One/Child", result)

    def test_list_path_can_descend_one_level(self):
        server = mcp_server.McpServer(self.root)
        result = server.siyuan_list({"path": "/Main/Projects/Doc One"})
        self.assertIn("| /Main/Projects/Doc One/Child | `doc3` | read_write | 30 | 1 | 2026-05-01 | 0 |", result)

    def test_list_notebooks_shows_effective_permission(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "notebook", "id": "nb1", "permission": "read_only"}],
            ),
        )
        server = mcp_server.McpServer(self.root)
        result = server.siyuan_list({})
        self.assertIn("| Main | `nb1` | read_only |", result)

    def test_list_documents_shows_effective_permission(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "document", "id": "doc1", "permission": "read_only"}],
            ),
        )
        server = mcp_server.McpServer(self.root)
        result = server.siyuan_list({"path": "/Main/Projects"})
        self.assertIn("| /Main/Projects/Doc One | `doc1` | read_only |", result)

    def test_list_paginates_direct_children(self):
        server = mcp_server.McpServer(self.root)
        result = server.siyuan_list({"path": "/Main/Projects", "limit": 1})
        self.assertIn("| /Main/Projects/Doc One | `doc1`", result)
        self.assertNotIn("| /Main/Projects/Hidden | `doc2`", result)
        self.assertIn("还有 1 项未显示。", result)
        self.assertIn('siyuan_list(path="/Main/Projects", offset=1, limit=1)', result)

    def test_list_documents_shows_document_tags(self):
        base = self.root / "knowledge_base"
        docs = [
            {
                "id": "doc1",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Projects/Doc One",
                "title": "Doc One",
                "path": "/doc1.sy",
                "tags": ["参考", "商业"],
                "word_count": 123,
                "block_count": 4,
                "updated": "20260501010101",
            },
            {
                "id": "doc3",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Projects/Doc Two",
                "title": "Doc Two",
                "path": "/doc3.sy",
                "tags": [],
                "word_count": 30,
                "block_count": 1,
                "updated": "20260501010103",
            },
        ]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs),
            encoding="utf-8",
        )
        server = mcp_server.McpServer(self.root)
        result = server.siyuan_list({"path": "/Main/Projects"})
        self.assertIn("| /Main/Projects/Doc One | `doc1` | read_write | 123 | 4 | 2026-05-01 | 0 | #参考# #商业# |", result)
        self.assertIn("| /Main/Projects/Doc Two | `doc3` | read_write | 30 | 1 | 2026-05-01 | 0 |  |", result)
        self.assertNotIn("tag：无", result)

    def test_find_documents_shows_document_tags(self):
        base = self.root / "knowledge_base"
        docs = [
            {
                "id": "doc1",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Projects/Doc One",
                "title": "Doc One",
                "path": "/doc1.sy",
                "tags": ["参考", "商业"],
                "word_count": 123,
                "block_count": 4,
                "updated": "20260501010101",
            },
        ]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs),
            encoding="utf-8",
        )
        client = FakeSearchClient([
            {
                "id": "block1",
                "rootID": "doc1",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "正文里有机器人这个词。",
                "content": "正文里有机器人这个词。",
                "hPath": "/Projects/Doc One",
                "path": "/doc1.sy",
            }
        ])
        output = self.run_find(client, {"query": "机器人", "scope": "full"})

        self.assertIn("tag：#参考# #商业#", output)

    def test_find_documents_omits_tag_line_without_tags(self):
        client = FakeSearchClient([
            {
                "id": "block1",
                "rootID": "doc3",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "正文里有机器人这个词。",
                "content": "正文里有机器人这个词。",
                "hPath": "/Projects/Doc One/Child",
                "path": "/doc3.sy",
            }
        ])
        output = self.run_find(client, {"query": "机器人", "scope": "full"})

        self.assertIn("`doc3`", output)
        self.assertNotIn("tag：", output)

    def test_find_documents_uses_live_full_text_blocks(self):
        client = FakeSearchClient([
            {
                "id": "block1",
                "rootID": "doc1",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "正文里有机器人这个词。",
                "content": "正文里有<mark>机器人</mark>这个词。",
                "hPath": "/Projects/Doc One",
                "path": "/doc1.sy",
            }
        ])
        output = self.run_find(client, {"query": "机器人", "scope": "full", "notebooks": "nb1"})

        self.assertIn("doc1", output)
        self.assertIn("正文里有机器人这个词", output)
        self.assertIn("实时搜索", output)
        self.assertEqual(client.seen_payloads[0]["paths"], ["nb1"])
        self.assertEqual(client.seen_payloads[0]["group_by"], 0)
        self.assertEqual(client.seen_payloads[0]["method"], 1)

    def test_find_documents_quotes_hyphenated_query_before_siyuan_search(self):
        client = FakeSearchClient([])
        self.run_find(client, {"query": "Scale-out", "scope": "full"})

        self.assertEqual(client.seen_payloads[0]["query"], '"Scale-out"')
        self.assertEqual(client.seen_payloads[0]["method"], 1)

    def test_find_documents_does_not_quote_regex_query(self):
        client = FakeSearchClient([])
        self.run_find(client, {"query": "Scale-out", "mode": "regex", "scope": "full"})

        self.assertEqual(client.seen_payloads[0]["query"], "Scale-out")
        self.assertEqual(client.seen_payloads[0]["method"], 3)

    def test_find_documents_plain_multi_term_query_adds_implicit_and_hint_with_results(self):
        client = FakeSearchClient([
            {
                "id": "block1",
                "rootID": "doc1",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "GPU 与光模块同时出现。",
                "content": "GPU 与光模块同时出现。",
                "hPath": "/Projects/Doc One",
                "path": "/doc1.sy",
            }
        ])
        output = self.run_find(client, {"query": "GPU 光模块", "scope": "full"})

        self.assertIn("doc1", output)
        self.assertIn("`GPU 光模块` 等价于 `GPU AND 光模块`", output)
        self.assertIn("`word1 OR word2`", output)

    def test_find_documents_plain_multi_term_query_adds_implicit_and_hint_without_results(self):
        output = self.run_find(FakeSearchClient([]), {"query": "GPU 光模块", "scope": "full"})

        self.assertIn("未找到匹配的可见文档", output)
        self.assertIn("`GPU 光模块` 等价于 `GPU AND 光模块`", output)

    def test_find_documents_does_not_add_implicit_and_hint_for_explicit_or(self):
        output = self.run_find(FakeSearchClient([]), {"query": "GPU OR 光模块", "scope": "full"})

        self.assertNotIn("空格表示 AND", output)

    def test_find_documents_does_not_add_implicit_and_hint_for_phrase_regex_or_sql(self):
        phrase = self.run_find(FakeSearchClient([]), {"query": '"GPU 光模块"', "scope": "full"})
        regex = self.run_find(FakeSearchClient([]), {"query": "GPU 光模块", "mode": "regex", "scope": "full"})
        sql = self.run_find(FakeSearchClient([]), {"query": "SELECT * FROM blocks", "mode": "sql", "scope": "full"})

        self.assertNotIn("空格表示 AND", phrase)
        self.assertNotIn("空格表示 AND", regex)
        self.assertNotIn("空格表示 AND", sql)

    def test_find_documents_accepts_keyword_as_query_compatibility_alias(self):
        client = FakeSearchClient([])
        output = self.run_find(client, {"keyword": "MCP 测试", "mode": "keyword", "scope": "full"})

        self.assertIn("未找到匹配的可见文档", output)
        self.assertIn("（full，query）", output)
        self.assertEqual(client.seen_payloads[0]["query"], "MCP 测试")
        self.assertEqual(client.seen_payloads[0]["method"], 1)

    def test_find_documents_accepts_keyword_parameter_as_query_alias(self):
        client = FakeSearchClient([])
        output = self.run_find(client, {"keyword": "MCP 测试", "scope": "full"})

        self.assertIn("未找到匹配的可见文档", output)
        self.assertEqual(client.seen_payloads[0]["query"], "MCP 测试")

    def test_find_documents_accepts_matching_query_and_keyword(self):
        client = FakeSearchClient([])
        output = self.run_find(client, {"query": "MCP 测试", "keyword": "MCP 测试", "scope": "full"})

        self.assertIn("未找到匹配的可见文档", output)
        self.assertEqual(client.seen_payloads[0]["query"], "MCP 测试")

    def test_find_documents_rejects_mismatched_query_and_keyword(self):
        client = FakeSearchClient([])
        with self.assertRaises(ValueError) as ctx:
            self.run_find(client, {"query": "MCP", "keyword": "测试", "scope": "full"})
        self.assertIn("query 与 keyword 不能同时传入不同值", str(ctx.exception))
        self.assertEqual(client.seen_payloads, [])

    def test_find_documents_requires_query_or_keyword_alias(self):
        client = FakeSearchClient([])
        with self.assertRaises(ValueError) as ctx:
            self.run_find(client, {"scope": "full"})
        self.assertIn("query 参数是必填的", str(ctx.exception))
        self.assertEqual(client.seen_payloads, [])

    def test_find_documents_keeps_all_matching_blocks_per_document(self):
        client = FakeSearchClient([
            {
                "id": "block1",
                "rootID": "doc1",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "第一个密匙在这里。",
                "content": "第一个<mark>密匙</mark>在这里。",
                "hPath": "/Projects/Doc One",
                "path": "/doc1.sy",
            },
            {
                "id": "block2",
                "rootID": "doc1",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "第二个密匙也在这里。",
                "content": "第二个<mark>密匙</mark>也在这里。",
                "hPath": "/Projects/Doc One",
                "path": "/doc1.sy",
            },
        ])
        output = self.run_find(client, {"query": "密匙", "scope": "full", "notebooks": "nb1"})

        self.assertIn("block1", output)
        self.assertIn("block2", output)
        self.assertIn("命中块：共 2 个，展示前 2 个。", output)
        self.assertIn("第一个密匙", output)
        self.assertIn("第二个密匙", output)

    def test_find_documents_limits_displayed_blocks_per_document(self):
        blocks = []
        for index in range(6):
            number = index + 1
            blocks.append({
                "id": f"block{number}",
                "rootID": "doc1",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": f"第{number}个密匙在这里。",
                "content": f"第{number}个<mark>密匙</mark>在这里。",
                "hPath": "/Projects/Doc One",
                "path": "/doc1.sy",
            })
        client = FakeSearchClient(blocks)
        output = self.run_find(client, {"query": "密匙", "scope": "full", "notebooks": "nb1"})

        self.assertIn("命中块：共 6 个，展示前 5 个。", output)
        self.assertIn("block5", output)
        self.assertNotIn("block6", output)

    def test_find_documents_allows_adjusting_displayed_blocks_per_document(self):
        blocks = []
        for index in range(6):
            number = index + 1
            blocks.append({
                "id": f"block{number}",
                "rootID": "doc1",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": f"第{number}个密匙在这里。",
                "content": f"第{number}个<mark>密匙</mark>在这里。",
                "hPath": "/Projects/Doc One",
                "path": "/doc1.sy",
            })
        client = FakeSearchClient(blocks)
        output = self.run_find(client, {
            "query": "密匙",
            "scope": "full",
            "notebooks": "nb1",
            "max_snippets_per_doc": 6,
        })

        self.assertIn("命中块：共 6 个，展示前 6 个。", output)
        self.assertIn("block6", output)

    def test_find_sql_drops_rows_outside_visible_index(self):
        base = self.root / "knowledge_base"
        visible = {
            "id": "doc1",
            "notebook_id": "nb1",
            "notebook_name": "Main",
            "hpath": "/Projects/Doc One",
            "title": "Doc One",
            "path": "/doc1.sy",
            "tags": [],
            "word_count": 123,
            "block_count": 4,
            "updated": "20260501010101",
        }
        (base / "docs.jsonl").write_text(
            json.dumps(visible, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        client = FakeSearchClient([], sql_rows=[
            {"id": "block-hidden", "content": "隐藏正文密匙"},
            {"id": "doc2", "content": "隐藏文档正文"},
            {"id": "doc3", "root_id": "doc3", "content": "子文档密匙"},
            {"content": "无身份正文"},
            {"id": "block1", "root_id": "doc1", "content": "可见正文不应出现"},
        ])
        client._blocks["doc1"] = [{"id": "block1", "type": "p", "markdown": "真实可见完整正文"}]
        client._blocks["doc2"] = [{"id": "block-hidden", "type": "p", "markdown": "真实隐藏正文"}]
        output = self.run_find(client, {"query": "SELECT id, content FROM blocks", "mode": "sql"})

        self.assertIn("真实可见完整正文", output)
        self.assertEqual(client.kramdown_reads, ["block1"])
        self.assertIn("`doc1`", output)
        self.assertIn("/Projects/Doc One", output)
        self.assertNotIn("隐藏正文密匙", output)
        self.assertNotIn("隐藏文档正文", output)
        self.assertNotIn("子文档密匙", output)
        self.assertNotIn("无身份正文", output)
        self.assertNotIn("可见正文不应出现", output)
        self.assertNotIn("doc2", output)
        self.assertNotIn("doc3", output)
        self.assertNotIn("block-hidden", output)

    def test_find_sql_still_filters_indexed_document_by_privacy_rule(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(ignore=[{"scope": "document", "id": "doc2"}], allow=[]),
        )
        client = FakeSearchClient([], sql_rows=[
            {"id": "block2", "root_id": "doc2", "content": "隐藏正文里有机器人"},
        ])
        client._blocks["doc2"] = [{"id": "block2", "type": "p", "markdown": "真实隐藏正文不读取"}]
        output = self.run_find(client, {"query": "SELECT id, content FROM blocks", "mode": "sql"})

        self.assertEqual(client.kramdown_reads, [])
        self.assertIn("未找到匹配的可见文档", output)
        self.assertNotIn("doc2", output)
        self.assertNotIn("隐藏正文里有机器人", output)

    def test_find_sql_preserves_interleaved_order_and_id_only_projection(self):
        client = FakeSearchClient([], sql_rows=[{"id": value} for value in ("A1", "B1", "A2", "B2")])
        client._blocks["doc1"] = [{"id": value, "type": "p", "markdown": f"真实正文-{value}"}
                                   for value in ("A1", "A2")]
        client._blocks["doc2"] = [{"id": value, "type": "p", "markdown": f"真实正文-{value}"}
                                   for value in ("B1", "B2")]
        output = self.run_find(client, {"query": "SELECT id FROM blocks ORDER BY updated", "mode": "sql"})
        positions = [output.index(f"真实正文-{value}") for value in ("A1", "B1", "A2", "B2")]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(client.kramdown_reads, ["A1", "B1", "A2", "B2"])
        self.assertIn("4 条可见结果，展示 4 条", output)
        self.assertTrue(any("WHERE id IN" in stmt for stmt in client.sql_statements))

    def test_find_sql_filters_canonical_identity_before_reading_any_body(self):
        write_privacy_rules_cache(self.root, PrivacyRules(
            ignore=[{"scope": "document", "id": "doc2"}], allow=[]))
        client = FakeSearchClient([], sql_rows=[
            {"id": "secret", "root_id": "doc1", "box": "nb1", "hpath": "/Projects/Doc One",
             "path": "/doc1.sy", "content": "伪装可见正文"},
            {"id": "public", "root_id": "doc2", "box": "hidden-nb", "hpath": "/secret",
             "path": "/doc2.sy", "content": "伪造隐藏正文", "markdown": "伪造隐藏Markdown"},
        ])
        client._blocks["doc2"] = [{"id": "secret", "type": "p", "markdown": "秘密真实正文"}]
        client._blocks["doc1"] = [{"id": "public", "type": "p", "markdown": "公开真实完整正文"}]
        output = self.run_find(client, {"query": "SELECT id, root_id, content FROM blocks", "mode": "sql"})
        self.assertEqual(client.kramdown_reads, ["public"])
        self.assertIn("公开真实完整正文", output)
        self.assertIn("1 条可见结果，展示 1 条", output)
        for secret in ("秘密真实正文", "伪装可见正文", "伪造隐藏正文", "伪造隐藏Markdown",
                       "`secret`", "hidden-nb", "/secret", "`doc2`", "隐藏 1", "过滤 1"):
            self.assertNotIn(secret, output)

    def test_find_sql_allows_read_only_but_hard_filters_privacy_rules(self):
        write_privacy_rules_cache(self.root, PrivacyRules(ignore=[], allow=[], permissions=[
            {"scope": "document", "id": "doc1", "permission": "read_only"}]))
        # Privacy Rules 按名称硬过滤，但仅限系统笔记本内；nb1 需伪装成系统笔记本。
        client = FakeSearchClient([], sql_rows=[{"id": "public"}, {"id": "privacy"}])
        client._hpaths["doc2"] = "/隐私规则"
        client.list_notebooks = lambda: [{"id": "nb1", "name": "思源桥", "closed": False}]
        client._blocks["doc1"] = [{"id": "public", "type": "p", "markdown": "只读真实正文"}]
        client._blocks["doc2"] = [{"id": "privacy", "type": "p", "markdown": "规则秘密正文"}]
        output = self.run_find(client, {"query": "SELECT id FROM blocks", "mode": "sql"})
        self.assertIn("只读真实正文", output)
        self.assertNotIn("规则秘密正文", output)
        self.assertNotIn("`doc2`", output)
        self.assertEqual(client.kramdown_reads, ["public"])
        self.assertEqual(client._snapshots, [])

    def test_find_sql_keeps_same_name_doc_outside_system_notebook_visible(self):
        write_privacy_rules_cache(self.root, PrivacyRules(ignore=[], allow=[]))
        # nb1 不是系统笔记本（名字 Main）：其下名为「隐私规则」的普通文档保持可见。
        client = FakeSearchClient([], sql_rows=[{"id": "privacy"}])
        client._hpaths["doc2"] = "/隐私规则"
        client._blocks["doc2"] = [{"id": "privacy", "type": "p", "markdown": "同名普通文档正文"}]
        output = self.run_find(client, {"query": "SELECT id FROM blocks", "mode": "sql"})
        self.assertIn("同名普通文档正文", output)
        self.assertIn("`doc2`", output)

    def test_find_sql_applies_twenty_hit_cap_after_visibility_and_ignores_snippet_limits(self):
        write_privacy_rules_cache(self.root, PrivacyRules(
            ignore=[{"scope": "document", "id": "doc2"}], allow=[]))
        ids = [f"hit-{number:02d}" for number in range(21)]
        client = FakeSearchClient([], sql_rows=[{"id": "secret"}, *({"id": value} for value in ids)])
        client._blocks["doc2"] = [{"id": "secret", "type": "p", "markdown": "秘密正文"}]
        client._blocks["doc1"] = [{"id": value, "type": "p", "markdown": f"完整正文-{value}"} for value in ids]
        output = self.run_find(client, {"query": "SELECT id FROM blocks", "mode": "sql",
                                       "limit": 1, "max_snippets_per_doc": 1})
        self.assertIn("21 条可见结果，展示 20 条", output)
        self.assertEqual(client.kramdown_reads, ids[:20])
        self.assertIn("完整正文-hit-19", output)
        self.assertNotIn("完整正文-hit-20", output)
        self.assertNotIn("秘密正文", output)
        self.assertIn("LIMIT/OFFSET", output)

    def test_find_sql_document_hit_counts_once_and_shows_only_twenty_display_blocks(self):
        client = FakeSearchClient([], sql_rows=[{"id": "doc1"}, {"id": "tail"}])
        client._blocks["doc1"] = [
            {"id": "embed", "type": "query_embed", "markdown": "{{SELECT id FROM blocks WHERE id='secret'}}"},
            {"id": "image", "type": "p", "markdown": "![图](assets/test.png)"},
            *({"id": f"p{number}", "type": "p", "markdown": f"文档段落-{number:02d}"}
              for number in range(2, 22)),
        ]
        client._blocks["doc2"] = [{"id": "tail", "type": "p", "markdown": "第二命中完整正文"}]
        with mock.patch.object(client, "get_asset", side_effect=AssertionError("SQL 不内联图片")):
            output = self.run_find(client, {"query": "SELECT id FROM blocks", "mode": "sql",
                                           "limit": 1, "max_snippets_per_doc": 1})
        self.assertIn("2 条可见结果，展示 2 条", output)
        self.assertIn("文档段落-19", output)
        self.assertNotIn("文档段落-20", output)
        self.assertIn("第二命中完整正文", output)
        self.assertIn("文档仅展示前 20 个块", output)
        self.assertIn("{{SELECT id FROM blocks WHERE id='secret'}}", output)
        self.assertIn("[图](assets/test.png)", output)
        self.assertNotIn("![图]", output)
        self.assertNotIn("base64", output)
        self.assertNotRegex(output, r"\[\d+\] id=")
        self.assertEqual(len(client.sql_statements), 2)  # 用户 SQL + canonical metadata；不执行嵌入 SQL

    def test_find_sql_returns_large_block_complete_without_snippet_truncation(self):
        body = "完整大块正文" * 3000 + "最终尾部标记"
        client = FakeSearchClient([], sql_rows=[{"id": "large", "content": "伪造短正文"}])
        client._blocks["doc1"] = [{"id": "large", "type": "p", "markdown": body}]
        output = self.run_find(client, {"query": "SELECT id FROM blocks", "mode": "sql"})
        self.assertIn(body, output)
        self.assertNotIn("伪造短正文", output)

    def test_find_sql_truncated_offset_uses_raw_candidates_without_hidden_counts(self):
        write_privacy_rules_cache(self.root, PrivacyRules(
            ignore=[{"scope": "document", "id": "doc2"}], allow=[]))
        client = FakeSearchClient([], sql_rows=[{"id": "secret"}, {"id": "public"}, {"id": "missing"}])
        client.sql_info = {"limit": 3, "truncated": True}
        client._blocks["doc1"] = [{"id": "public", "type": "p", "markdown": "可见正文"}]
        client._blocks["doc2"] = [{"id": "secret", "type": "p", "markdown": "秘密正文"}]
        output = self.run_find(client, {"query": "SELECT id FROM blocks ORDER BY updated", "mode": "sql"})
        self.assertIn("1 条可见结果，展示 1 条", output)
        self.assertIn("LIMIT 3 OFFSET 3", output)
        self.assertNotIn("OFFSET 1", output)
        self.assertIn("OFFSET 按原始候选数推进", output)
        self.assertIn("上游查询已截断", output)
        for value in ("秘密正文", "`secret`", "`missing`", "隐藏 1", "过滤 2"):
            self.assertNotIn(value, output)
        self.assertEqual(client.kramdown_reads, ["public"])

    def test_find_sql_explicit_limit_false_truncated_never_claims_exhaustion(self):
        client = FakeSearchClient([], sql_rows=[{"id": "one"}])
        client.sql_info = {"limit": 1, "truncated": False}
        client._blocks["doc1"] = [{"id": "one", "type": "p", "markdown": "真实正文"}]
        output = self.run_find(client, {"query": "SELECT id FROM blocks LIMIT 1", "mode": "sql"})
        self.assertIn("真实正文", output)
        for claim in ("全部查完", "全部查询完成", "已查完", "上游查询已截断", "OFFSET"):
            self.assertNotIn(claim, output)

    def test_find_sql_duplicate_rows_preserve_order_and_are_not_deduplicated(self):
        client = FakeSearchClient([], sql_rows=[{"id": value} for value in ("a", "b", "a")])
        client._blocks["doc1"] = [{"id": "a", "type": "p", "markdown": "甲真实正文"},
                                   {"id": "b", "type": "p", "markdown": "乙真实正文"}]
        output = self.run_find(client, {"query": "SELECT id FROM blocks", "mode": "sql"})
        self.assertEqual(client.kramdown_reads, ["a", "b", "a"])
        self.assertEqual(output.count("甲真实正文"), 2)
        self.assertLess(output.index("甲真实正文"), output.index("乙真实正文"))
        self.assertLess(output.index("乙真实正文"), output.rindex("甲真实正文"))
        self.assertIn("3 条可见结果，展示 3 条", output)

    def test_find_sql_notebook_filter_restores_closed_notebook_after_render(self):
        client = FakeSearchClient([], closed=True, sql_rows=[{"id": "public"}, {"id": "other"}])
        client._blocks["doc1"] = [{"id": "public", "type": "p", "markdown": "目标本正文"},
                                   {"id": "other", "box": "nb2", "type": "p", "markdown": "其他本正文"}]
        original = client.get_block_kramdown

        def read_while_open(block_id):
            self.assertFalse(client.closed)
            return original(block_id)

        with mock.patch.object(client, "get_block_kramdown", side_effect=read_while_open):
            output = self.run_find(client, {"query": "SELECT id FROM blocks", "mode": "sql", "notebooks": ["nb1"]})
        self.assertIn("目标本正文", output)
        self.assertNotIn("其他本正文", output)
        self.assertEqual(client.kramdown_reads, ["public"])
        self.assertEqual(client.opened, ["nb1"])
        self.assertEqual(client.closed_again, ["nb1"])
        self.assertTrue(client.closed)

    def test_find_sql_block_keeps_image_addresses_and_document_tags_without_inline(self):
        docs = mcp_server.load_docs(self.root)
        docs[0]["tags"] = ["研究", "图表"]
        (self.root / "knowledge_base" / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs), encoding="utf-8")
        body = '![本地图](assets/local.png)\n![网络图](https://example.com/chart.png "说明")'
        client = FakeSearchClient([], sql_rows=[{"id": "image-block"}])
        client._blocks["doc1"] = [{"id": "image-block", "type": "p", "markdown": body}]
        with mock.patch.object(client, "get_asset", side_effect=AssertionError("SQL 不下载图片")):
            output = self.run_find(client, {"query": "SELECT id FROM blocks", "mode": "sql"})
        self.assertIn("tag：#研究# #图表#", output)
        self.assertIn("[本地图](assets/local.png)", output)
        self.assertIn('[网络图](https://example.com/chart.png "说明")', output)
        self.assertNotIn("![", output)
        self.assertNotIn("base64", output)
        self.assertEqual(client.kramdown_reads, ["image-block"])

    def test_find_sql_missing_id_and_nonexistent_blocks_produce_no_output(self):
        client = FakeSearchClient([], sql_rows=[{"content": "无ID伪造正文"}, {"id": "missing", "root_id": "doc1"}])
        output = self.run_find(client, {"query": "SELECT id FROM blocks", "mode": "sql"})
        self.assertIn("0 条可见结果，展示 0 条", output)
        self.assertIn("未找到匹配的可见文档或块", output)
        self.assertNotIn("无ID伪造正文", output)
        self.assertNotIn("`missing`", output)
        self.assertEqual(client.kramdown_reads, [])

    def test_find_documents_filters_live_results_with_privacy_rules(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(ignore=[{"scope": "document", "id": "doc2"}], allow=[]),
        )
        client = FakeSearchClient([
            {
                "id": "block2",
                "rootID": "doc2",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "隐藏正文里有机器人。",
                "content": "隐藏正文里有<mark>机器人</mark>。",
                "hPath": "/Projects/Hidden",
                "path": "/doc2.sy",
            }
        ])
        output = self.run_find(client, {"query": "机器人", "scope": "full", "notebooks": "nb1"})

        self.assertIn("未找到匹配的可见文档", output)
        self.assertNotIn("doc2", output)

    def test_find_documents_document_privacy_hides_child_live_results(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(ignore=[{"scope": "document", "id": "doc1"}], allow=[]),
        )
        client = FakeSearchClient([
            {
                "id": "block3",
                "rootID": "doc3",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "子文档里有密匙。",
                "content": "子文档里有<mark>密匙</mark>。",
                "hPath": "/Projects/Doc One/Child",
                "path": "/doc1/doc3.sy",
            }
        ])
        output = self.run_find(client, {"query": "密匙", "scope": "full", "notebooks": "nb1"})

        self.assertIn("未找到匹配的可见文档", output)
        self.assertNotIn("doc3", output)

    def test_find_documents_filters_notebook_name_rules_with_live_names(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(ignore=[{"scope": "notebook", "name": "Main"}], allow=[]),
        )
        client = FakeSearchClient([
            {
                "id": "block1",
                "rootID": "doc1",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "正文里有机器人。",
                "content": "正文里有<mark>机器人</mark>。",
                "hPath": "/Projects/Doc One",
                "path": "/doc1.sy",
            }
        ])
        output = self.run_find(client, {"query": "机器人", "scope": "full", "notebooks": "nb1"})

        self.assertIn("未找到匹配的可见文档", output)
        self.assertNotIn("doc1", output)

    def test_find_documents_temporarily_opens_closed_notebooks(self):
        client = FakeSearchClient([
            {
                "id": "block1",
                "rootID": "doc1",
                "box": "nb1",
                "type": "NodeParagraph",
                "markdown": "关闭笔记本里的机器人。",
                "content": "关闭笔记本里的<mark>机器人</mark>。",
                "hPath": "/Projects/Doc One",
                "path": "/doc1.sy",
            }
        ], closed=True)
        output = self.run_find(client, {"query": "机器人", "scope": "full", "notebooks": "nb1"})

        self.assertIn("doc1", output)
        self.assertEqual(client.opened, ["nb1"])
        self.assertEqual(client.closed_again, ["nb1"])

    def run_find(self, client: FakeSearchClient, args: dict[str, Any]) -> str:
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        return server.siyuan_find(args)


class ParseTagsInputTests(unittest.TestCase):
    def test_parses_pairs_with_various_separators(self):
        cases = {
            "#甲# #乙#": ["甲", "乙"],
            "#甲#，#乙#": ["甲", "乙"],
            "#甲#、#乙#": ["甲", "乙"],
            "#甲#;#乙#": ["甲", "乙"],
            "#甲#；#乙#": ["甲", "乙"],
            "#甲#,#乙#": ["甲", "乙"],
            "#甲##乙#": ["甲", "乙"],
            "#甲#  #乙#": ["甲", "乙"],
            "# 甲 #": ["甲"],
            "#标签 名称#": ["标签 名称"],
            "#甲＃乙#": ["甲＃乙"],
            "#A/B/C#": ["A/B/C"],
            "#甲# #甲# #乙#": ["甲", "乙"],
            "": [],
            "，、 ": [],
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(mcp_server.parse_tags_input(raw), expected)

    def test_rejects_unrecognized_input(self):
        for raw in ["#甲", "甲#", "##甲#", "#甲#乙#", "甲,乙", "甲", "＃甲＃"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    mcp_server.parse_tags_input(raw)

    def test_rejects_empty_tag_name(self):
        with self.assertRaises(ValueError) as ctx:
            mcp_server.parse_tags_input("# #")
        self.assertIn("空标签", str(ctx.exception))

    def test_rejects_forbidden_characters_and_lists_them(self):
        with self.assertRaises(ValueError) as ctx:
            mcp_server.parse_tags_input("#甲(1)#")
        message = str(ctx.exception)
        self.assertIn("包含禁止字符", message)
        self.assertIn("「(」", message)
        self.assertIn("完整禁止字符清单", message)
        self.assertIn("无法全局搜索", message)

    def test_rejects_comma_inside_tag_name(self):
        with self.assertRaises(ValueError) as ctx:
            mcp_server.parse_tags_input("#甲,乙#")
        self.assertIn("「,」", str(ctx.exception))


class Fts5QueryTokenQuoteTests(unittest.TestCase):
    def test_quote_fts5_query_tokens(self):
        cases = [
            ("Scale-out", '"Scale-out"'),
            ("GPU 光模块 NVLink", "GPU 光模块 NVLink"),
            ("Scale-out AND NVLink", '"Scale-out" AND NVLink'),
            ('"Scale-out"', '"Scale-out"'),
            ("(Scale-out OR NVLink)", '("Scale-out" OR NVLink)'),
            ("foo-bar*", '"foo-bar"*'),
            ("AND", "AND"),
            ("and", "and"),
            ('"AND"', '"AND"'),
            ("2026-08-01", '"2026-08-01"'),
            ("foo/bar", '"foo/bar"'),
            ("NVLink", "NVLink"),
            ('"unclosed', '"unclosed'),
            ("Scale-out Scale-up", '"Scale-out" "Scale-up"'),
            ("Scale-out*", '"Scale-out"*'),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(mcp_server.quote_fts5_query_tokens(raw), expected)


class McpServerWriteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / ".test_tmp" / "mcp_write"
        shutil.rmtree(self.root, ignore_errors=True)
        base = self.root / "knowledge_base"
        base.mkdir(parents=True, exist_ok=True)
        (base / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "Main"}], ensure_ascii=False),
            encoding="utf-8",
        )
        write_privacy_rules_cache(self.root, PrivacyRules(ignore=[], allow=[]))
        self.asset_dir = self.root / "local-assets"
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        docs = [
            {
                "id": "doc1",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Projects/Doc One",
                "title": "Doc One",
                "path": "/doc1.sy",
                "tags": [],
                "word_count": 123,
                "block_count": 2,
                "updated": "20260501010101",
            },
            {
                "id": "doc3",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Projects/Doc One/Child",
                "title": "Child",
                "path": "/doc3.sy",
                "tags": [],
                "word_count": 30,
                "block_count": 1,
                "updated": "20260501010103",
            },
        ]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs),
            encoding="utf-8",
        )
        self._original_load_agent_notebook = mcp_server.load_agent_notebook

        def fake_load_agent_notebook(_client, _root, config_language=None):
            return mcp_server.AgentNotebookState(
                language=config_language or "zh-CN",
                notebook_id="system-nb",
                notebook_name="思源桥",
                document_ids={
                    "ai_guide": ("system-guide",),
                    "mcp_usage_guide": (),
                    "workspace_index_guide": (),
                    "workspace_index": (),
                    "about": ("system-about",),
                    "privacy_rules": ("system-pr",),
                },
                ai_guide_markdown="",
                workspace_index_markdown="",
                privacy_rules=PrivacyRules(ignore=[], allow=[]),
            )

        mcp_server.load_agent_notebook = fake_load_agent_notebook

    def tearDown(self):
        mcp_server.load_agent_notebook = self._original_load_agent_notebook

    def _make_client(self, query_sql_blocks=None):
        """Create a FakeSearchClient with optional block data for SQL queries."""
        client = FakeSearchClient([])
        if query_sql_blocks:
            doc_id = list(query_sql_blocks.keys())[0] if query_sql_blocks else "doc1"
            client._blocks = query_sql_blocks
        return client

    def _server_and_client(self, query_sql_blocks=None):
        client = self._make_client(query_sql_blocks)
        server = mcp_server.McpServer(self.root)
        original = mcp_server.detect_active_profile

        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        return server, client, original

    def test_create_document_refuses_unconfirmed(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "notebook_id": "nb1",
                    "title": "New Doc",
                    "markdown": "# Hello",
                    "confirmed": False,
                })
            self.assertIn("confirmed", str(ctx.exception))
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_refuses_hidden_notebook(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "notebook_id": "nb-hidden",
                    "title": "New Doc",
                    "markdown": "# Hello",
                    "confirmed": True,
                })
            self.assertIn("不可见", str(ctx.exception))
            self.assertIn("create_notebook", str(ctx.exception))
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_missing_path_notebook_suggests_create_notebook(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "title": "New Doc",
                    "path": "/Missing Notebook/New Doc",
                    "markdown": "# Hello",
                    "confirmed": True,
                })
            self.assertIn('action="create_notebook"', str(ctx.exception))
            self.assertIn('notebook_name="Missing Notebook"', str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._created_docs)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_creates_snapshot_before_write(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_create({
                "notebook_id": "nb1",
                "title": "New Doc",
                "markdown": "# Hello\n\nWorld",
                "confirmed": True,
            })
            self.assertIn("New Doc", result)
            self.assertIn("created", result)
            self.assertEqual(len(client._snapshots), 1)
            self.assertIn("siyuan-bridge:auto-snapshot", client._snapshots[0]["memo"])
            self.assertIn("tool=siyuan_create", client._snapshots[0]["memo"])
            self.assertIn("target=/Main/New Doc", client._snapshots[0]["memo"])
            self.assertIn("New Doc", client._push_msgs[0])
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_auto_refresh_uses_system_context(self):
        server, _client, original_detect = self._server_and_client()
        original_refresh = mcp_server.refresh_index
        calls: list[dict[str, Any]] = []

        def fake_refresh(_client, _root, **kwargs):
            calls.append(kwargs)
            return None

        mcp_server.refresh_index = fake_refresh
        try:
            result = server.siyuan_create({
                "notebook_id": "nb1",
                "title": "New Doc",
                "markdown": "Body",
                "confirmed": True,
            })
            self.assertIn("路径已同步", result)
            self.assertEqual(calls[-1]["system_notebook_id"], "system-nb")
            self.assertEqual(calls[-1]["privacy_rules_doc_ids"], {"system-pr"})
        finally:
            mcp_server.refresh_index = original_refresh
            mcp_server.detect_active_profile = original_detect

    def test_create_document_uses_given_path(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_create({
                "notebook_id": "nb1",
                "title": "My Doc",
                "path": "/custom/path",
                "markdown": "content",
                "confirmed": True,
            })
            self.assertIn("custom/path", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_full_path_resolves_notebook_and_internal_path(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_create({
                "title": "New Doc",
                "path": "/Main/Projects/New Doc",
                "markdown": "content",
                "confirmed": True,
            })
            self.assertEqual(client._created_docs, [("nb1", "/Projects/New Doc", "content")])
            self.assertIn("/Main/Projects/New Doc", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_ambiguous_notebook_name_requires_notebook_id(self):
        base = self.root / "knowledge_base"
        (base / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "Main"}, {"id": "nb2", "name": "Main"}], ensure_ascii=False),
            encoding="utf-8",
        )
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "title": "New Doc",
                    "path": "/Main/Projects/New Doc",
                    "markdown": "content",
                    "confirmed": True,
                })
            self.assertIn("notebook_id", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._created_docs)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_existing_path_rejects_by_default(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "title": "Doc One",
                    "path": "/Main/Projects/Doc One",
                    "markdown": "replacement",
                    "confirmed": True,
                })
            self.assertIn("if_exists=overwrite", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._created_docs)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_rejects_live_path_missing_from_cached_index(self):
        server, client, original = self._server_and_client()
        client._hpaths["external-doc"] = "/Projects/External"
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "title": "External",
                    "path": "/Main/Projects/External",
                    "markdown": "must not create a duplicate",
                    "if_exists": "reject",
                    "confirmed": True,
                })
            self.assertEqual(
                getattr(ctx.exception, "error_code", None),
                "conflict:already_exists",
            )
            self.assertIn("`external-doc`", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._created_docs)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_existing_path_can_overwrite_preserving_doc_id(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Old first.", "parent_id": "doc1", "sort": 1},
                {"id": "block2", "type": "p", "markdown": "Old second.", "parent_id": "doc1", "sort": 2},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_create({
                "title": "Doc One",
                "path": "/Main/Projects/Doc One",
                "markdown": "Fresh content.",
                "if_exists": "overwrite",
                "confirmed": True,
            })
            self.assertEqual(client._created_docs, [])
            self.assertEqual(client._deleted_blocks, ["block2", "block1"])
            self.assertEqual(client._appended_blocks, [("doc1", "Fresh content.")])
            self.assertIn("`doc1`", result)
            self.assertIn("overwritten", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_existing_path_can_create_new_same_name(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_create({
                "title": "Doc One",
                "path": "/Main/Projects/Doc One",
                "markdown": "Another document.",
                "if_exists": "create_new",
                "confirmed": True,
            })
            self.assertEqual(client._created_docs, [("nb1", "/Projects/Doc One", "Another document.")])
            self.assertIn("created_new", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_rejects_read_only_notebook(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "notebook", "id": "nb1", "permission": "read_only"}],
            ),
        )
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "title": "New Doc",
                    "path": "/Main/New Doc",
                    "markdown": "# Hi",
                    "confirmed": True,
                })
            self.assertIn("read_write", str(ctx.exception))
            self.assertFalse(client._created_docs)
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def _write_markdown_file(self, name: str, content: str, encoding: str = "utf-8") -> str:
        path = self.root / name
        path.write_text(content, encoding=encoding)
        return str(path)

    def test_create_document_from_markdown_file(self):
        server, client, original = self._server_and_client()
        file_path = self._write_markdown_file("import.md", "# Imported Title\n\nBody from file.")
        try:
            result = server.siyuan_create({
                "notebook_id": "nb1",
                "title": "Imported Doc",
                "markdown_file": file_path,
                "confirmed": True,
            })
            self.assertEqual(client._created_docs, [("nb1", "/Imported Doc", "# Imported Title\n\nBody from file.")])
            self.assertIn("Imported Doc", result)
            self.assertEqual(len(client._snapshots), 1)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_from_gbk_markdown_file(self):
        server, client, original = self._server_and_client()
        file_path = self._write_markdown_file("gbk.md", "# 中文标题\n\n正文内容", encoding="gbk")
        try:
            server.siyuan_create({
                "notebook_id": "nb1",
                "title": "GBK Doc",
                "markdown_file": file_path,
                "confirmed": True,
            })
            self.assertEqual(client._created_docs, [("nb1", "/GBK Doc", "# 中文标题\n\n正文内容")])
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_markdown_and_file_mutually_exclusive(self):
        server, client, original = self._server_and_client()
        file_path = self._write_markdown_file("import.md", "Body")
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "notebook_id": "nb1",
                    "title": "Doc",
                    "markdown": "inline",
                    "markdown_file": file_path,
                    "confirmed": True,
                })
            self.assertIn("只能填写一个", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._created_docs)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_requires_markdown_or_file(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "notebook_id": "nb1",
                    "title": "Doc",
                    "confirmed": True,
                })
            self.assertIn("markdown", str(ctx.exception))
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_markdown_file_missing_raises_before_snapshot(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "notebook_id": "nb1",
                    "title": "Doc",
                    "markdown_file": str(self.root / "does-not-exist.md"),
                    "confirmed": True,
                })
            self.assertIn("无法读取文件", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._created_docs)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_after_from_markdown_file(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor text."},
            ]
        }
        file_path = self._write_markdown_file("insert.md", "Inserted from file.")
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "insert_after",
                "start_index": 1,
                "start_id": "block1",
                "markdown_file": file_path,
                "confirmed": True,
            })
            self.assertEqual(client._inserted_after, [("block1", "Inserted from file.")])
            self.assertIn("insert_after", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_markdown_and_file_mutually_exclusive(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor text."},
            ]
        }
        file_path = self._write_markdown_file("insert.md", "From file.")
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "insert_after",
                    "start_index": 1,
                    "start_id": "block1",
                    "markdown": "inline",
                    "markdown_file": file_path,
                    "confirmed": True,
                })
            self.assertIn("只能填写一个", str(ctx.exception))
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_single_block_replace_from_multi_block_file_rejected(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor text."},
            ]
        }
        file_path = self._write_markdown_file("multi.md", "First.\n\nSecond.")
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "single_block_replace",
                    "start_index": 1,
                    "start_id": "block1",
                    "markdown_file": file_path,
                    "confirmed": True,
                })
            self.assertIn("default_block_replace", str(ctx.exception))
            self.assertNotIn("multi_block_replace", str(ctx.exception))
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def _write_import_assets(self) -> Path:
        folder = self.root / "import-assets"
        folder.mkdir(exist_ok=True)
        (folder / "pic.png").write_bytes(b"png")
        (folder / "note.pdf").write_bytes(b"pdf")
        (folder / "pack.zip").write_bytes(b"zip")
        (folder / "chapter.md").write_text("# Chapter\n", encoding="utf-8")
        (folder / "files").mkdir(exist_ok=True)
        return folder

    def test_create_markdown_file_uploads_local_links(self):
        folder = self._write_import_assets()
        md = self.root / "post.md"
        md.write_text(
            "\n".join([
                "See ![图](./import-assets/pic.png)",
                "",
                "[报告](./import-assets/note.pdf)",
                "",
                "[章节](./import-assets/chapter.md)",
                "",
                "[外链](https://example.com/a.png)",
                "",
                "[目录](./import-assets/files)",
                "",
                f"<file:///{(folder / 'pack.zip').as_posix()}>",
            ]),
            encoding="utf-8",
        )
        server, client, original = self._server_and_client()
        client.force_empty_child_reads = 2
        try:
            result = server.siyuan_create({
                "notebook_id": "nb1",
                "title": "Imported Assets",
                "markdown_file": str(md),
                "confirmed": True,
            })
            self.assertGreaterEqual(client._empty_child_reads, 1)
            uploaded = [paths for _, paths, _ in client._inserted_assets]
            self.assertTrue(any(str(folder / "pic.png") in item for batch in uploaded for item in batch))
            self.assertTrue(any(str(folder / "note.pdf") in item for batch in uploaded for item in batch))
            self.assertTrue(any(str(folder / "chapter.md") in item for batch in uploaded for item in batch))
            self.assertTrue(any(str(folder / "pack.zip") in item for batch in uploaded for item in batch))
            self.assertTrue(any(str(folder / "files") in item for batch in uploaded for item in batch))
            self.assertIn("assets/pic.png", result)
            self.assertIn("https://example.com/a.png", "\n".join(
                str(block.get("markdown", ""))
                for block in client._blocks.get("new-doc-0", [])
            ) or "")
            updated = "\n".join(md for _, md in client._updated_blocks)
            self.assertIn("assets/pic.png", updated)
            self.assertIn("assets/note.pdf", updated)
            self.assertNotIn("https://example.com/a.png", "".join(path for batch in uploaded for path in batch))
        finally:
            mcp_server.detect_active_profile = original

    def test_create_markdown_file_skips_code_block_and_missing_and_large(self):
        folder = self._write_import_assets()
        (folder / "big.bin").write_bytes(b"12345")
        md = self.root / "mixed.md"
        md.write_text(
            "\n".join([
                "```",
                "![代码](./import-assets/pic.png)",
                "```",
                "",
                "![缺](./import-assets/missing.png)",
                "",
                "![大](./import-assets/big.bin)",
            ]),
            encoding="utf-8",
        )
        server, client, original = self._server_and_client()
        try:
            with mock.patch.object(mcp_server, "ASSET_LARGE_FILE_THRESHOLD_BYTES", 4):
                result = server.siyuan_create({
                    "notebook_id": "nb1",
                    "title": "Mixed",
                    "markdown_file": str(md),
                    "confirmed": True,
                })
            self.assertFalse(client._inserted_assets)
            self.assertIn("未找到", result)
            self.assertIn("超过 20 MB", result)
            self.assertNotIn("成功：", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_markdown_file_converts_unique_heading_anchor(self):
        md = self.root / "anchor.md"
        md.write_text("## 安装说明\n\n请看[这里](#安装说明)", encoding="utf-8")
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_create({
                "notebook_id": "nb1",
                "title": "Guide",
                "markdown_file": str(md),
                "confirmed": True,
            })
            self.assertIn("锚点已转换", result)
            self.assertTrue(any("siyuan://blocks/" in markdown for _, markdown in client._updated_blocks))
        finally:
            mcp_server.detect_active_profile = original

    def test_create_markdown_file_keeps_duplicate_heading_anchor(self):
        md = self.root / "dup-anchor.md"
        md.write_text("## 安装说明\n\n## 安装说明\n\n请看[这里](#安装说明)", encoding="utf-8")
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_create({
                "notebook_id": "nb1",
                "title": "Guide",
                "markdown_file": str(md),
                "confirmed": True,
            })
            self.assertIn("锚点重名未转换", result)
            self.assertFalse(client._updated_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_plain_markdown_does_not_scan_local_files(self):
        folder = self._write_import_assets()
        server, client, original = self._server_and_client()
        try:
            server.siyuan_create({
                "notebook_id": "nb1",
                "title": "Plain",
                "markdown": f"![图]({(folder / 'pic.png').as_posix()})",
                "confirmed": True,
            })
            self.assertFalse(client._inserted_assets)
            self.assertFalse(client._updated_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_edit_markdown_file_processes_only_new_blocks(self):
        folder = self._write_import_assets()
        (self.root / "old.png").write_bytes(b"old")
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "![旧](./old.png)"},
            ]
        }
        md = self.root / "insert-assets.md"
        md.write_text("![新](./import-assets/pic.png)", encoding="utf-8")
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "insert_after",
                "start_index": 1,
                "start_id": "block1",
                "markdown_file": str(md),
                "confirmed": True,
            })
            uploaded = [path for _, paths, _ in client._inserted_assets for path in paths]
            self.assertEqual(uploaded, [str(folder / "pic.png")])
            self.assertIn("assets/pic.png", result)
            self.assertIn("![旧](./old.png)", client._blocks["doc1"][0]["markdown"])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_rename_creates_snapshot(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "rename",
                "new_title": "Renamed",
                "confirmed": True,
            })
            self.assertEqual(client._renamed_docs, [("doc1", "Renamed")])
            self.assertEqual(len(client._snapshots), 1)
            self.assertIn("siyuan_doc_manage", client._snapshots[0]["memo"])
            self.assertIn("已重命名为", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_set_tags_writes_comma_separated_names(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "set_tags",
                "tags": "#甲#，#乙# #A/B#",
                "confirmed": True,
            })
            self.assertEqual(client._set_attrs_calls, [("doc1", {"tags": "甲,乙,A/B"})])
            self.assertEqual(len(client._snapshots), 1)
            self.assertIn("设置成功：共 3 个标签", result)
            self.assertIn("1. #甲#", result)
            self.assertIn("2. #乙#", result)
            self.assertIn("3. #A/B#", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_set_tags_empty_clears_all(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "set_tags",
                "tags": "",
                "confirmed": True,
            })
            self.assertEqual(client._set_attrs_calls, [("doc1", {"tags": ""})])
            self.assertIn("已清除全部文档标签（0 个）", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_set_tags_requires_tags_param(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One",
                    "action": "set_tags",
                    "confirmed": True,
                })
            self.assertIn("需要 tags 参数", str(ctx.exception))
            self.assertEqual(client._set_attrs_calls, [])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_set_tags_rejects_forbidden_before_snapshot(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One",
                    "action": "set_tags",
                    "tags": "#甲(1)#",
                    "confirmed": True,
                })
            message = str(ctx.exception)
            self.assertIn("包含禁止字符", message)
            self.assertIn("「(」", message)
            self.assertIn("完整禁止字符清单", message)
            self.assertFalse(client._snapshots)
            self.assertEqual(client._set_attrs_calls, [])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_set_tags_requires_read_write(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "document", "id": "doc1", "permission": "read_only"}],
            ),
        )
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One",
                    "action": "set_tags",
                    "tags": "#甲#",
                    "confirmed": True,
                })
            self.assertIn("不允许 set_tags", str(ctx.exception))
            self.assertIn("read_only", str(ctx.exception))
            self.assertEqual(client._set_attrs_calls, [])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_auto_refresh_uses_system_context(self):
        server, _client, original_detect = self._server_and_client()
        original_refresh = mcp_server.refresh_index
        calls: list[dict[str, Any]] = []

        def fake_refresh(_client, _root, **kwargs):
            calls.append(kwargs)
            return None

        mcp_server.refresh_index = fake_refresh
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "rename",
                "new_title": "Renamed",
                "confirmed": True,
            })
            self.assertIn("路径已同步", result)
            self.assertEqual(calls[-1]["system_notebook_id"], "system-nb")
            self.assertEqual(calls[-1]["privacy_rules_doc_ids"], {"system-pr"})
        finally:
            mcp_server.refresh_index = original_refresh
            mcp_server.detect_active_profile = original_detect

    def test_wait_for_hpath_requires_sql_index_source_sync(self):
        server, client, _original = self._server_and_client()
        client._hpaths["doc1"] = "/Projects/Renamed"

        def stale_query_sql(stmt):
            text = str(stmt).casefold()
            if "from blocks" in text and ("type='d'" in text or "type = 'd'" in text):
                return [{
                    "id": "doc1",
                    "box": "nb1",
                    "hpath": "/Projects/Doc One",
                    "path": "/doc1.sy",
                    "name": "Doc One",
                    "type": "d",
                    "updated": "20260501010101",
                }]
            return FakeSearchClient.query_sql(client, stmt)

        original_timeout = mcp_server.POST_WRITE_SYNC_TIMEOUT
        original_interval = mcp_server.POST_WRITE_SYNC_INTERVAL
        client.query_sql = stale_query_sql
        mcp_server.POST_WRITE_SYNC_TIMEOUT = 0.01
        mcp_server.POST_WRITE_SYNC_INTERVAL = 0.01
        try:
            status = server._wait_for_hpath(client, "doc1", "/Projects/Renamed")
            self.assertFalse(status.ok)
            self.assertIn("索引源：/Projects/Doc One", status.detail)
        finally:
            mcp_server.POST_WRITE_SYNC_TIMEOUT = original_timeout
            mcp_server.POST_WRITE_SYNC_INTERVAL = original_interval
            mcp_server.detect_active_profile = _original

    def test_siyuan_doc_manage_move_to_notebook(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "move",
                "target_parent": "/Main",
                "confirmed": True,
            })
            self.assertEqual(client._moved_docs, [(["doc1"], "nb1")])
            self.assertIn("已移动到", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_create_notebook(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_doc_manage({
                "action": "create_notebook",
                "notebook_name": "Research",
                "confirmed": True,
            })
            self.assertEqual(
                client._created_notebooks,
                [{"id": "nb-Research", "name": "Research", "closed": False}],
            )
            self.assertEqual(len(client._snapshots), 1)
            self.assertIn("tool=siyuan_doc_manage", client._snapshots[0]["memo"])
            self.assertIn("target=/Research", client._snapshots[0]["memo"])
            self.assertIn("# 笔记本已创建", result)
            self.assertIn("`nb-Research`", result)
            self.assertIn("/Research/<文档标题>", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_create_notebook_requires_confirmed(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "action": "create_notebook",
                    "notebook_name": "Research",
                    "confirmed": False,
                })
            self.assertIn("confirmed", str(ctx.exception))
            self.assertFalse(client._created_notebooks)
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_create_notebook_rejects_duplicate_name(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "action": "create_notebook",
                    "notebook_name": "main",
                    "confirmed": True,
                })
            self.assertEqual(getattr(ctx.exception, "error_code", None), "conflict:already_exists")
            self.assertIn("同名笔记本已存在", str(ctx.exception))
            self.assertFalse(client._created_notebooks)
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_delete_requires_confirmed(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One",
                    "action": "delete",
                    "confirmed": False,
                })
            self.assertIn("confirmed", str(ctx.exception))
            self.assertFalse(client._removed_docs)
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_delete_removes_doc(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "delete",
                "confirmed": True,
            })
            self.assertEqual(client._removed_docs, ["doc1"])
            self.assertIn("可通过思源快照", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_copy_allows_read_only_source(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "document", "id": "doc1", "permission": "read_only"}],
            ),
        )
        server, client, original = self._server_and_client()
        client._docs["doc1"] = "# Source\n\nBody"
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "copy",
                "target_path": "/Main/Doc Copy",
                "confirmed": True,
            })
            self.assertEqual(client._duplicated_docs, ["doc1"])
            self.assertEqual(client._renamed_docs, [("duplicated-1", "Doc Copy")])
            self.assertEqual(client._moved_docs, [(["duplicated-1"], "nb1")])
            self.assertFalse(client._created_docs)
            self.assertIn("已复制到", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_copy_requires_target_path(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One",
                    "action": "copy",
                    "target_title": "Doc Copy",
                    "confirmed": True,
                })
            self.assertIn("target_path", str(ctx.exception))
            self.assertFalse(client._duplicated_docs)
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_delete_rejects_read_only_descendant(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "document", "id": "doc3", "permission": "read_only"}],
            ),
        )
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One",
                    "action": "delete",
                    "confirmed": True,
                })
            self.assertIn("子文档中存在只读或隐藏文档", str(ctx.exception))
            self.assertNotIn("doc3", str(ctx.exception))
            self.assertNotIn("read_only:", str(ctx.exception))
            self.assertFalse(client._removed_docs)
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_move_allows_read_only_descendant(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "document", "id": "doc3", "permission": "read_only"}],
            ),
        )
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "move",
                "target_parent": "/Main",
                "confirmed": True,
            })
            self.assertEqual(client._moved_docs, [(["doc1"], "nb1")])
            self.assertIn("已移动到", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_move_rejects_read_only_ancestor(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "document", "id": "doc1", "permission": "read_only"}],
            ),
        )
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One/Child",
                    "action": "move",
                    "target_parent": "/Main",
                    "confirmed": True,
                })
            self.assertIn("read_only", str(ctx.exception))
            self.assertFalse(client._moved_docs)
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_doc_manage_ancestor_helper_rejects_read_only_parent(self):
        privacy = PrivacyRules(
            ignore=[],
            allow=[],
            permissions=[{"scope": "document", "id": "doc1", "permission": "read_only"}],
        )
        docs = mcp_server.load_docs(self.root)
        doc = next(item for item in docs if str(item.get("id")) == "doc3")
        server = mcp_server.McpServer(self.root)
        with self.assertRaises(ValueError) as ctx:
            server._ensure_doc_manage_ancestors_writable(doc, privacy, docs, action="move")
        self.assertIn("祖先路径权限不是 read_write", str(ctx.exception))

    def test_siyuan_doc_manage_rename_rejects_read_only_source(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "document", "id": "doc1", "permission": "read_only"}],
            ),
        )
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One",
                    "action": "rename",
                    "new_title": "Nope",
                    "confirmed": True,
                })
            self.assertIn("read_only", str(ctx.exception))
            self.assertFalse(client._renamed_docs)
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_rejects_read_only_document(self):
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(
                ignore=[],
                allow=[],
                permissions=[{"scope": "document", "id": "doc1", "permission": "read_only"}],
            ),
        )
        server, client, original = self._server_and_client({
            "doc1": [
                {"id": "b1", "parent_id": "doc1", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "Old", "content": "", "sort": 1},
            ]
        })
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "single_block_replace",
                    "start_index": 1,
                    "start_id": "b1",
                    "markdown": "New",
                    "confirmed": True,
                })
            self.assertIn("read_only", str(ctx.exception))
            self.assertFalse(client._updated_blocks)
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_export_writes_markdown_without_snapshot(self):
        server, client, original = self._server_and_client()
        client._docs["doc1"] = "# Exported\n\nBody"
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "export",
            })
            self.assertIn("文档已导出", result)
            self.assertIn("自包含目录", result)
            self.assertFalse(client._snapshots)
            export_dir = self.root / "ai_workspace" / "exports" / "Main_Projects_Doc One"
            self.assertTrue(export_dir.is_dir())
            exported_md = export_dir / "Main_Projects_Doc One.md"
            self.assertTrue(exported_md.exists())
            self.assertEqual(exported_md.read_text(encoding="utf-8"), "# Exported\n\nBody")
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_export_rewrites_asset_links_relative(self):
        server, client, original = self._server_and_client()
        client._docs["doc1"] = "![图](assets/a.png)\n\n[报告](assets/a.pdf)"
        try:
            server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "export",
            })
            exported_md = self.root / "ai_workspace" / "exports" / "Main_Projects_Doc One" / "Main_Projects_Doc One.md"
            text = exported_md.read_text(encoding="utf-8")
            self.assertIn("](./assets/a.png)", text)
            self.assertIn("](./assets/a.pdf)", text)
            self.assertNotIn("](assets/", text)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_read_rejects_stale_document_path(self):
        server, client, original = self._server_and_client({
            "doc1": [
                {"id": "block1", "parent_id": "doc1", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "Body", "content": "", "sort": 1},
            ]
        })
        client._hpaths["doc1"] = "/Projects/Renamed"
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_read({"document": "/Main/Projects/Doc One"})
            self.assertIn("文档路径已过期", str(ctx.exception))
            self.assertIn("/Main/Projects/Renamed", str(ctx.exception))
            self.assertIn("siyuan_operate", str(ctx.exception))
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_rejects_stale_document_path_before_snapshot(self):
        server, client, original = self._server_and_client({
            "doc1": [
                {"id": "block1", "parent_id": "doc1", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "Old", "content": "", "sort": 1},
            ]
        })
        client._hpaths["doc1"] = "/Projects/Renamed"
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "single_block_replace",
                    "start_index": 1,
                    "start_id": "block1",
                    "markdown": "New",
                    "confirmed": True,
                })
            self.assertIn("文档路径已过期", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._updated_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_rejects_stale_document_path_before_snapshot(self):
        server, client, original = self._server_and_client()
        client._hpaths["doc1"] = "/Projects/Renamed"
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One",
                    "action": "rename",
                    "new_title": "Next",
                    "confirmed": True,
                })
            self.assertIn("文档路径已过期", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._renamed_docs)
        finally:
            mcp_server.detect_active_profile = original

    def test_document_id_bypasses_stale_path_check(self):
        server, client, original = self._server_and_client({
            "doc1": [
                {"id": "block1", "parent_id": "doc1", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "Body", "content": "", "sort": 1},
            ]
        })
        client._hpaths["doc1"] = "/Projects/Renamed"
        try:
            result = server.siyuan_read({"document_id": "doc1"})
            self.assertIn("Body", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_single_block_replace_uses_path_index_and_block_id(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Original text."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "single_block_replace",
                "start_index": 1,
                "start_id": "block1",
                "markdown": "Replaced text.",
                "confirmed": True,
            })
            self.assertIn("siyuan_edit", client._snapshots[0]["memo"])
            self.assertEqual(client._updated_blocks, [("block1", "Replaced text.")])
            self.assertIn("single_block_replace", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_rejects_index_id_mismatch(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Original text."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "single_block_replace",
                    "start_index": 1,
                    "start_id": "wrong-block",
                    "markdown": "Replaced text.",
                    "confirmed": True,
                })
            self.assertIn("目标块校验失败", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._updated_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_default_block_replace_range_inserts_then_deletes_old_range(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "First."},
                {"id": "block2", "type": "p", "markdown": "Second."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "default_block_replace",
                "start_index": 1,
                "start_id": "block1",
                "end_index": 2,
                "end_id": "block2",
                "markdown": "New range.",
                "confirmed": True,
            })
            self.assertEqual(client._inserted_before, [("block1", "New range.")])
            self.assertEqual(client._deleted_blocks, ["block2", "block1"])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_delete_checks_entire_subtree_references(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Root body.", "parent_id": "doc1"},
            ],
            "doc3": [
                {"id": "child-block", "type": "p", "markdown": "Child body.", "parent_id": "doc3"},
            ],
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        client._hpaths["refdoc"] = "/References/External Ref"
        client._refs = [{
            "def_block_id": "child-block",
            "block_id": "external-ref-block",
            "root_id": "refdoc",
            "type": "block-link",
            "content": "External link to child document content.",
            "markdown": "External [child](siyuan://blocks/child-block).",
            "block_type": "p",
        }]
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_doc_manage({
                    "document": "/Main/Projects/Doc One",
                    "action": "delete",
                    "confirmed": True,
                })
            self.assertIn("被引用块 `child-block`", str(ctx.exception))
            self.assertIn("/Main/References/External Ref", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._removed_docs)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_doc_manage_delete_ignores_references_inside_same_deleted_subtree(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Root body.", "parent_id": "doc1"},
            ],
            "doc3": [
                {"id": "child-ref", "type": "p", "markdown": "Internal ((block1)).", "parent_id": "doc3"},
            ],
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        client._refs = [{
            "def_block_id": "block1",
            "block_id": "child-ref",
            "root_id": "doc3",
            "type": "textmark",
            "content": "Internal reference.",
            "markdown": "Internal ((block1)).",
            "block_type": "p",
        }]
        try:
            result = server.siyuan_doc_manage({
                "document": "/Main/Projects/Doc One",
                "action": "delete",
                "confirmed": True,
            })
            self.assertEqual(client._removed_docs, ["doc1"])
            self.assertIn("已删除文档", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_create_overwrite_rejects_when_body_block_is_referenced(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Old first.", "parent_id": "doc1", "sort": 1},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        client._hpaths["refdoc"] = "/References/Visible Ref"
        client._refs = [{
            "def_block_id": "block1",
            "block_id": "refblock",
            "root_id": "refdoc",
            "type": "textmark",
            "content": "This paragraph cites the old block.",
            "markdown": "This paragraph cites ((block1)).",
            "block_type": "p",
        }]
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "title": "Doc One",
                    "path": "/Main/Projects/Doc One",
                    "markdown": "Fresh content.",
                    "if_exists": "overwrite",
                    "confirmed": True,
                })
            self.assertEqual(getattr(ctx.exception, "error_code", None), "conflict:referenced_blocks")
            self.assertIn("/Main/References/Visible Ref", str(ctx.exception))
            self.assertIn("This paragraph cites the old block.", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._deleted_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_default_block_replace_summary_filters_stale_deleted_blocks(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "First."},
                {"id": "block2", "type": "p", "markdown": "Second."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)

        def delayed_delete(block_id):
            client._deleted_blocks.append(block_id)

        client.delete_block = delayed_delete
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "default_block_replace",
                "start_index": 1,
                "start_id": "block1",
                "end_index": 2,
                "end_id": "block2",
                "markdown": "New first.\n\nNew second.",
                "confirmed": True,
            })
            new_content = result.split("## 新内容", 1)[1]
            self.assertIn("New first.", new_content)
            self.assertIn("New second.", new_content)
            self.assertNotIn("id=block1", new_content)
            self.assertNotIn("id=block2", new_content)
            self.assertNotIn("First.", new_content)
            self.assertNotIn("Second.", new_content)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_default_block_replace_retries_transient_deleted_heading_read(self):
        blocks = {
            "doc1": [
                {"id": "heading1", "type": "h", "markdown": "## Old heading", "parent_id": "doc1"},
                {"id": "child1", "type": "p", "markdown": "Old child.", "parent_id": "heading1"},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        original_get_child_blocks = client.get_child_blocks
        stale_root_reads = 1

        def get_child_blocks(block_id):
            nonlocal stale_root_reads
            if block_id == "doc1" and client._deleted_blocks and stale_root_reads:
                stale_root_reads -= 1
                return [{"id": "heading1", "type": "h", "markdown": "## Old heading", "parent_id": "doc1"}]
            if block_id == "heading1" and client._deleted_blocks:
                raise SiYuanApiError("block not found or its encrypted notebook is locked")
            return original_get_child_blocks(block_id)

        client.get_child_blocks = get_child_blocks
        try:
            with mock.patch.object(mcp_server.time, "sleep") as sleep:
                result = server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "default_block_replace",
                    "start_index": 1,
                    "start_id": "heading1",
                    "markdown": "## New heading",
                    "confirmed": True,
                })
            self.assertIn("# 文档已编辑", result)
            self.assertIn("## New heading", result)
            sleep.assert_called_once_with(mcp_server.POST_WRITE_SYNC_INTERVAL)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_default_block_replace_checks_descendant_block_references(self):
        blocks = {
            "doc1": [
                {"id": "heading1", "type": "h", "markdown": "## Heading", "parent_id": "doc1"},
                {"id": "child1", "type": "p", "markdown": "Child content.", "parent_id": "heading1"},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        client._hpaths["refdoc"] = "/References/Visible Ref"
        client._refs = [{
            "def_block_id": "child1",
            "block_id": "refblock",
            "root_id": "refdoc",
            "type": "textmark",
            "content": "Reference to the heading child.",
            "markdown": "Reference ((child1)).",
            "block_type": "p",
        }]
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "default_block_replace",
                    "start_index": 1,
                    "start_id": "heading1",
                    "markdown": "Replacement.",
                    "confirmed": True,
                })
            self.assertIn("被引用块 `child1`", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._inserted_before)
            self.assertFalse(client._deleted_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_default_block_replace_can_replace_single_block_with_multi_block_markdown(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor."},
                {"id": "block2", "type": "p", "markdown": "After."},
            ]
        }
        markdown = "### New heading\n\nNew paragraph.\n\n```python\nprint('ok')\n```"
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "default_block_replace",
                "start_index": 1,
                "start_id": "block1",
                "markdown": markdown,
                "confirmed": True,
            })
            self.assertEqual(client._updated_blocks, [])
            self.assertEqual(client._inserted_before, [("block1", markdown)])
            self.assertEqual(client._deleted_blocks, ["block1"])
            self.assertIn("## 新内容", result)
            self.assertIn("New heading", result)
            self.assertIn("New paragraph.", result)
            self.assertIn("type=code language=python", result)
            self.assertNotIn("After.\n\n如需回滚", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_single_block_replace_rejects_multi_block_markdown(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "single_block_replace",
                    "start_index": 1,
                    "start_id": "block1",
                    "markdown": "First.\n\nSecond.",
                    "confirmed": True,
                })
            self.assertIn("default_block_replace", str(ctx.exception))
            self.assertNotIn("multi_block_replace", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._updated_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_accepts_legacy_multi_block_replace_alias(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor."},
                {"id": "block2", "type": "p", "markdown": "After."},
            ]
        }
        markdown = "### New heading\n\nNew paragraph."
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "multi_block_replace",
                "start_index": 1,
                "start_id": "block1",
                "markdown": markdown,
                "confirmed": True,
            })
            self.assertEqual(client._inserted_before, [("block1", markdown)])
            self.assertEqual(client._deleted_blocks, ["block1"])
            self.assertIn("action：default_block_replace", result)
            self.assertNotIn("multi_block_replace", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_edit_set_cell(self):
        table = "| 指标 | 当前值 |\n|---|---|\n| 股价 | 旧值 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "set_cell",
                    "row": 1,
                    "column": "当前值",
                    "value": "232.30",
                    "expected_old_value": "旧值",
                },
                "confirmed": True,
            })
            self.assertEqual(
                client._updated_blocks,
                [("table1", "| 指标 | 当前值 |\n| --- | --- |\n| 股价 | 232.30 |")],
            )
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_edit_set_header_cell_with_coordinates(self):
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "set_cell",
                    "cell": {
                        "row": 0,
                        "column_index": 1,
                        "value": "Metric",
                        "expected_old_value": "A",
                    },
                },
                "confirmed": True,
            })
            self.assertEqual(
                client._updated_blocks,
                [("table1", "| Metric | B |\n| --- | --- |\n| 1 | 2 |")],
            )
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_edit_set_multiple_cells(self):
        table = "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "set_cell",
                    "cells": [
                        {"row": 1, "column_index": 2, "value": "20", "expected_old_value": "2"},
                        {"row": 2, "column_index": 3, "value": "60", "expected_old_value": "6"},
                    ],
                },
                "confirmed": True,
            })
            self.assertEqual(
                client._updated_blocks,
                [("table1", "| A | B | C |\n| --- | --- | --- |\n| 1 | 20 | 3 |\n| 4 | 5 | 60 |")],
            )
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_edit_preserves_escaped_pipes(self):
        table = r"| A | B |\n| --- | --- |\n| one\|two | 2 |".replace("\\n", "\n")
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "set_cell",
                    "cell": {"row": 1, "column_index": 2, "value": "changed"},
                },
                "confirmed": True,
            })
            self.assertEqual(
                client._updated_blocks,
                [("table1", r"| A | B |\n| --- | --- |\n| one\|two | changed |".replace("\\n", "\n"))],
            )
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_delete_allows_superblock(self):
        blocks = {
            "doc1": [
                {"id": "super1", "type": "s", "markdown": ""},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "delete",
                "start_index": 1,
                "start_id": "super1",
                "confirmed": True,
            })
            self.assertEqual(client._deleted_blocks, ["super1"])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_after_single_block(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor text."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "insert_after",
                "start_index": 1,
                "start_id": "block1",
                "markdown": "Inserted after anchor.",
                "confirmed": True,
            })
            self.assertIn("siyuan_edit", client._snapshots[0]["memo"])
            self.assertEqual(client._inserted_after, [("block1", "Inserted after anchor.")])
            self.assertIn("insert_after", result)
            self.assertIn("block1", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_assets_uploads_multiple_items_in_order(self):
        image = self.asset_dir / "chart.TIFF"
        file_path = self.asset_dir / "README.md"
        folder = self.asset_dir / "source files"
        image.write_bytes(b"image")
        file_path.write_text("readme", encoding="utf-8")
        folder.mkdir()
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor text."},
                {"id": "block2", "type": "p", "markdown": "Next text."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = json.loads(server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "insert_assets",
                "start_index": 1,
                "start_id": "block1",
                "assets": [
                    {"local_path": str(image), "title": "季度图"},
                    {"local_path": str(file_path), "name": "说明文件", "title": "悬停提示"},
                    {"local_path": str(folder), "name": "源文件目录"},
                ],
                "confirmed": True,
            }))

            self.assertTrue(result["ok"])
            self.assertEqual([item["kind"] for item in result["inserted"]], ["image", "file", "directory"])
            self.assertEqual(result["inserted"][0]["name"], "chart")
            self.assertEqual(client._inserted_assets[0][0], "doc1")
            inserted_markdown = client._inserted_after[0][1]
            self.assertLess(inserted_markdown.index("chart.TIFF"), inserted_markdown.index("README.md"))
            self.assertLess(inserted_markdown.index("README.md"), inserted_markdown.index("source files"))
            self.assertIn('![chart](assets/chart.TIFF "季度图")', inserted_markdown)
            self.assertIn('[说明文件](assets/README.md "悬停提示")', inserted_markdown)
            self.assertIn("[源文件目录](<file://", inserted_markdown)
            self.assertEqual(len(client._snapshots), 1)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_assets_blank_name_uses_official_defaults(self):
        image = self.asset_dir / "photo.avif"
        other = self.asset_dir / "photo.heic"
        image.write_bytes(b"image")
        other.write_bytes(b"other")
        blocks = {"doc1": [{"id": "block1", "type": "p", "markdown": "Anchor."}]}
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = json.loads(server.siyuan_edit({
                "document_id": "doc1",
                "action": "insert_assets",
                "start_index": 1,
                "start_id": "block1",
                "assets": [
                    {"local_path": str(image), "name": "", "title": ""},
                    {"local_path": str(other)},
                ],
                "confirmed": True,
            }))
            self.assertEqual(result["inserted"][0]["kind"], "image")
            self.assertEqual(result["inserted"][0]["name"], "photo")
            self.assertEqual(result["inserted"][1]["kind"], "file")
            self.assertEqual(result["inserted"][1]["name"], "photo.heic")
            markdown = client._inserted_after[0][1]
            self.assertIn("![photo](assets/photo.avif)", markdown)
            self.assertIn("[photo.heic](assets/photo.heic)", markdown)
            self.assertNotIn('""', markdown)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_assets_rejects_duplicate_basenames_before_snapshot(self):
        first_dir = self.asset_dir / "a"
        second_dir = self.asset_dir / "b"
        first_dir.mkdir()
        second_dir.mkdir()
        first = first_dir / "Report.txt"
        second = second_dir / "report.TXT"
        first.write_text("a", encoding="utf-8")
        second.write_text("b", encoding="utf-8")
        blocks = {"doc1": [{"id": "block1", "type": "p", "markdown": "Anchor."}]}
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document_id": "doc1",
                    "action": "insert_assets",
                    "start_index": 1,
                    "start_id": "block1",
                    "assets": [
                        {"local_path": str(first)},
                        {"local_path": str(second)},
                    ],
                    "confirmed": True,
                })
            self.assertIn("拆成不同调用", str(ctx.exception))
            self.assertEqual(client._snapshots, [])
            self.assertEqual(client._inserted_assets, [])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_assets_large_file_pauses_before_snapshot(self):
        large = self.asset_dir / "large.bin"
        large.write_bytes(b"12")
        blocks = {"doc1": [{"id": "block1", "type": "p", "markdown": "Anchor."}]}
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with mock.patch.object(mcp_server, "ASSET_LARGE_FILE_THRESHOLD_BYTES", 1):
                result = json.loads(server.siyuan_edit({
                    "document_id": "doc1",
                    "action": "insert_assets",
                    "start_index": 1,
                    "start_id": "block1",
                    "assets": [{"local_path": str(large)}],
                    "confirmed": True,
                }))
            self.assertFalse(result["ok"])
            self.assertTrue(result["requires_confirmation"])
            self.assertEqual(result["large_files"][0]["size_bytes"], 2)
            self.assertEqual(client._snapshots, [])
            self.assertEqual(client._inserted_assets, [])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_assets_large_file_flag_allows_upload(self):
        large = self.asset_dir / "large.bin"
        large.write_bytes(b"12")
        blocks = {"doc1": [{"id": "block1", "type": "p", "markdown": "Anchor."}]}
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with mock.patch.object(mcp_server, "ASSET_LARGE_FILE_THRESHOLD_BYTES", 1):
                result = json.loads(server.siyuan_edit({
                    "document_id": "doc1",
                    "action": "insert_assets",
                    "start_index": 1,
                    "start_id": "block1",
                    "assets": [{"local_path": str(large)}],
                    "upload_large_files": True,
                    "confirmed": True,
                }))
            self.assertTrue(result["ok"])
            self.assertEqual(len(client._inserted_assets), 1)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_assets_stale_anchor_does_not_upload(self):
        file_path = self.asset_dir / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        blocks = {"doc1": [{"id": "block1", "type": "p", "markdown": "Anchor."}]}
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError):
                server.siyuan_edit({
                    "document_id": "doc1",
                    "action": "insert_assets",
                    "start_index": 1,
                    "start_id": "stale-id",
                    "assets": [{"local_path": str(file_path)}],
                    "confirmed": True,
                })
            self.assertEqual(client._snapshots, [])
            self.assertEqual(client._inserted_assets, [])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_assets_rejects_range_anchor_before_snapshot(self):
        file_path = self.asset_dir / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor."},
                {"id": "block2", "type": "p", "markdown": "Second."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document_id": "doc1",
                    "action": "insert_assets",
                    "start_index": 1,
                    "start_id": "block1",
                    "end_index": 2,
                    "end_id": "block2",
                    "assets": [{"local_path": str(file_path)}],
                    "confirmed": True,
                })
            self.assertIn("一次只支持", str(ctx.exception))
            self.assertEqual(client._snapshots, [])
            self.assertEqual(client._inserted_assets, [])
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_assets_validation_failure_compensates_document_blocks(self):
        file_path = self.asset_dir / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        blocks = {"doc1": [{"id": "block1", "type": "p", "markdown": "Anchor."}]}
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        real_build = mcp_server.build_display_blocks
        call_count = 0

        def fail_one_readback(fake_client, root_id, *, include_block_ids=False):
            nonlocal call_count
            call_count += 1
            result = real_build(fake_client, root_id, include_block_ids=include_block_ids)
            if call_count == 2:
                return [block for block in result if block.id == "block1"]
            return result

        try:
            with mock.patch.object(mcp_server, "build_display_blocks", side_effect=fail_one_readback):
                with self.assertRaises(ValueError) as ctx:
                    server.siyuan_edit({
                        "document_id": "doc1",
                        "action": "insert_assets",
                        "start_index": 1,
                        "start_id": "block1",
                        "assets": [{"local_path": str(file_path)}],
                        "confirmed": True,
                    })
            self.assertIn("已删除 1 个", str(ctx.exception))
            self.assertIn("程序未自动删除", str(ctx.exception))
            self.assertEqual(len(client._deleted_blocks), 1)
            self.assertEqual(client._blocks["doc1"][0]["id"], "block1")
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_before_single_block(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor text."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "insert_before",
                "start_index": 1,
                "start_id": "block1",
                "markdown": "Inserted before anchor.",
                "confirmed": True,
            })
            self.assertIn("siyuan_edit", client._snapshots[0]["memo"])
            self.assertEqual(client._inserted_before, [("block1", "Inserted before anchor.")])
            self.assertIn("insert_before", result)
            self.assertIn("block1", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_append_document_end(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "append",
                "markdown": "Appended content.",
                "confirmed": True,
            })
            self.assertIn("siyuan_edit", client._snapshots[0]["memo"])
            self.assertEqual(client._appended_blocks, [("doc1", "Appended content.")])
            self.assertIn("append", result)
            self.assertIn("Appended content.", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_delete_single_block(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Text to delete."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "delete",
                "start_index": 1,
                "start_id": "block1",
                "confirmed": True,
            })
            self.assertIn("siyuan_edit", client._snapshots[0]["memo"])
            self.assertEqual(client._deleted_blocks, ["block1"])
            self.assertIn("delete", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_delete_rejects_visible_reference(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Text to delete.", "parent_id": "doc1"},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        client._hpaths["refdoc"] = "/References/Visible Ref"
        client._refs = [{
            "def_block_id": "block1",
            "block_id": "refblock",
            "root_id": "refdoc",
            "type": "textmark",
            "content": "Visible citing paragraph.",
            "markdown": "Visible ((block1)).",
            "block_type": "p",
        }]
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "delete",
                    "start_index": 1,
                    "start_id": "block1",
                    "confirmed": True,
                })
            message = str(ctx.exception)
            self.assertEqual(getattr(ctx.exception, "error_code", None), "conflict:referenced_blocks")
            self.assertIn("被引用块 `block1`", message)
            self.assertIn("/Main/References/Visible Ref", message)
            self.assertIn("引用块：`refblock`", message)
            self.assertIn("Visible citing paragraph.", message)
            self.assertIn("如何处理这些被引用块", message)
            self.assertIn("仍是同一个事实、观点、任务或条目", message)
            self.assertIn("重新规划操作", message)
            self.assertIn('reference_policy="break"', message)
            self.assertFalse(client._snapshots)
            self.assertFalse(client._deleted_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_delete_break_allows_explicitly_confirmed_reference_damage(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Text to delete.", "parent_id": "doc1"},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        client._hpaths["refdoc"] = "/References/Visible Ref"
        client._refs = [{
            "def_block_id": "block1",
            "block_id": "refblock",
            "root_id": "refdoc",
            "type": "textmark",
            "content": "Visible citing paragraph.",
            "markdown": "Visible ((block1)).",
            "block_type": "p",
        }]
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "delete",
                "start_index": 1,
                "start_id": "block1",
                "reference_policy": "break",
                "confirmed": True,
            })
            self.assertEqual(client._deleted_blocks, ["block1"])
            self.assertEqual(len(client._snapshots), 1)
            self.assertIn("用户已明确允许破坏引用", result)
            self.assertIn("影响 1 处引用", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_delete_hides_protected_reference_details(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Text to delete.", "parent_id": "doc1"},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        client._hpaths["secret-doc"] = "/Private/Secret Ref"
        client._refs = [{
            "def_block_id": "block1",
            "block_id": "secret-ref-block",
            "root_id": "secret-doc",
            "type": "textmark",
            "content": "Highly secret citing paragraph.",
            "markdown": "Secret ((block1)).",
            "block_type": "p",
        }]
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(ignore=[{"scope": "document", "id": "secret-doc"}], allow=[]),
        )
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "delete",
                    "start_index": 1,
                    "start_id": "block1",
                    "confirmed": True,
                })
            message = str(ctx.exception)
            self.assertIn("1 篇受保护文档", message)
            self.assertNotIn("Secret Ref", message)
            self.assertNotIn("secret-ref-block", message)
            self.assertNotIn("Highly secret", message)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_delete_range(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "First."},
                {"id": "block2", "type": "p", "markdown": "Second."},
                {"id": "block3", "type": "p", "markdown": "Third."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "delete",
                "start_index": 1,
                "start_id": "block1",
                "end_index": 3,
                "end_id": "block3",
                "confirmed": True,
            })
            self.assertEqual(client._deleted_blocks, ["block3", "block2", "block1"])
            self.assertIn("delete", result)
            self.assertIn("3 个块", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_single_block_replace_rejects_attachment(self):
        blocks = {
            "doc1": [
                {"id": "img1", "type": "p", "markdown": "![img](assets/img.png)"},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "single_block_replace",
                    "start_index": 1,
                    "start_id": "img1",
                    "markdown": "Try replace attachment.",
                    "confirmed": True,
                })
            self.assertIn("type=attachment", str(ctx.exception))
            self.assertFalse(client._snapshots)
            self.assertFalse(client._updated_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_insert_row_before(self):
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "insert_row_before",
                    "row": 1,
                    "values": {"A": "new", "B": "row"},
                },
                "confirmed": True,
            })
            new_table = client._updated_blocks[0][1]
            self.assertIn("new", new_table)
            self.assertIn("row", new_table)
            self.assertLess(new_table.index("new"), new_table.index("1"))
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_insert_row_new_operation(self):
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "insert_row",
                    "row": 0,
                    "position": "after",
                    "values": ["new", "row"],
                },
                "confirmed": True,
            })
            self.assertEqual(
                client._updated_blocks,
                [("table1", "| A | B |\n| --- | --- |\n| new | row |\n| 1 | 2 |")],
            )
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_insert_row_after(self):
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "insert_row_after",
                    "row": 2,
                    "values": ["x", "y"],
                },
                "confirmed": True,
            })
            new_table = client._updated_blocks[0][1]
            self.assertIn("x", new_table)
            self.assertIn("y", new_table)
            pos_3 = new_table.index("| 3 ")
            pos_x = new_table.index("x")
            self.assertLess(pos_3, pos_x)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_delete_row(self):
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "delete_row",
                    "row": 1,
                },
                "confirmed": True,
            })
            new_table = client._updated_blocks[0][1]
            self.assertNotIn("| 1 | 2 |", new_table)
            self.assertIn("| 3 | 4 |", new_table)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_insert_column(self):
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "insert_column",
                    "column_index": 1,
                    "position": "after",
                    "values": ["C", "x"],
                },
                "confirmed": True,
            })
            self.assertEqual(
                client._updated_blocks,
                [("table1", "| A | C | B |\n| --- | --- | --- |\n| 1 | x | 2 |\n| 3 |  | 4 |")],
            )
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_delete_column(self):
        table = "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "delete_column",
                    "column_index": 2,
                },
                "confirmed": True,
            })
            self.assertEqual(
                client._updated_blocks,
                [("table1", "| A | C |\n| --- | --- |\n| 1 | 3 |")],
            )
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_delete_last_column_rejected_before_snapshot(self):
        table = "| A |\n| --- |\n| 1 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError):
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "table_edit",
                    "start_index": 1,
                    "start_id": "table1",
                    "table_edit": {
                        "operation": "delete_column",
                        "column_index": 1,
                    },
                    "confirmed": True,
                })
            self.assertFalse(client._snapshots)
            self.assertFalse(client._updated_blocks)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_edit_rejects_non_table(self):
        blocks = {
            "doc1": [
                {"id": "p1", "type": "p", "markdown": "Not a table."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_edit({
                    "document": "/Main/Projects/Doc One",
                    "action": "table_edit",
                    "start_index": 1,
                    "start_id": "p1",
                    "table_edit": {
                        "operation": "set_cell",
                        "row": 1,
                        "column": "A",
                        "value": "x",
                    },
                    "confirmed": True,
                })
            self.assertIn("table", str(ctx.exception).casefold())
            self.assertFalse(client._snapshots)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_single_block_replace_returns_original_and_readback_content(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Original text."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "single_block_replace",
                "start_index": 1,
                "start_id": "block1",
                "markdown": "Replaced text.",
                "confirmed": True,
            })
            self.assertIn("## 原内容", result)
            self.assertIn("Original text.", result)
            self.assertIn("## 新内容", result)
            self.assertIn("Replaced text.", result)
            self.assertIn("[1] id=block1 type=paragraph", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_insert_after_returns_inserted_blocks_read_back(self):
        blocks = {
            "doc1": [
                {"id": "block1", "type": "p", "markdown": "Anchor text."},
                {"id": "block2", "type": "p", "markdown": "Next text."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "insert_after",
                "start_index": 1,
                "start_id": "block1",
                "markdown": "Inserted paragraph.\n\n```python\nprint('x')\n```",
                "confirmed": True,
            })
            self.assertIn("## 锚点内容", result)
            self.assertIn("Anchor text.", result)
            self.assertIn("## 插入内容", result)
            self.assertIn("Inserted paragraph.", result)
            self.assertIn("type=code language=python", result)
            self.assertNotIn("Next text.", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_delete_returns_deleted_content_and_context(self):
        blocks = {
            "doc1": [
                {"id": "before", "type": "p", "markdown": "Before delete."},
                {"id": "target", "type": "p", "markdown": "Delete me."},
                {"id": "after", "type": "p", "markdown": "After delete."},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "delete",
                "start_index": 2,
                "start_id": "target",
                "confirmed": True,
            })
            self.assertIn("## 已删除内容", result)
            self.assertIn("Delete me.", result)
            self.assertIn("## 当前上下文", result)
            self.assertIn("Before delete.", result)
            self.assertIn("After delete.", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_siyuan_edit_table_edit_returns_old_and_new_table(self):
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        blocks = {
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        }
        server, client, original = self._server_and_client(query_sql_blocks=blocks)
        try:
            result = server.siyuan_edit({
                "document": "/Main/Projects/Doc One",
                "action": "table_edit",
                "start_index": 1,
                "start_id": "table1",
                "table_edit": {
                    "operation": "set_cell",
                    "row": 1,
                    "column": "B",
                    "value": "updated",
                },
                "confirmed": True,
            })
            self.assertIn("## 原表格", result)
            self.assertIn("| 1 | 2 |", result)
            self.assertIn("## 新表格", result)
            self.assertIn("| 1 | updated |", result)
        finally:
            mcp_server.detect_active_profile = original

    def test_normalize_markdown_strips_duplicate_h1(self):
        result = mcp_server.normalize_new_document_markdown(
            "My Title",
            "# My Title\n\nBody text.",
        )
        self.assertEqual(result, "\nBody text.")

    def test_normalize_markdown_keeps_different_h1(self):
        result = mcp_server.normalize_new_document_markdown(
            "My Title",
            "# Different Title\n\nBody text.",
        )
        self.assertEqual(result, "# Different Title\n\nBody text.")

    def test_normalize_markdown_skips_leading_empty_lines(self):
        result = mcp_server.normalize_new_document_markdown(
            "My Title",
            "\n\n# My Title\n\nBody text.",
        )
        self.assertEqual(result, "\n\n\nBody text.")

    def test_normalize_markdown_ignores_h2(self):
        result = mcp_server.normalize_new_document_markdown(
            "My Title",
            "## My Title\n\nBody text.",
        )
        self.assertEqual(result, "## My Title\n\nBody text.")

    def test_create_document_strips_duplicate_h1(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_create({
                "notebook_id": "nb1",
                "title": "My Doc",
                "markdown": "# My Doc\n\nContent here.",
                "confirmed": True,
            })
            self.assertIn("created", result)
            self.assertIn("Content here.", client._docs["new-doc-0"])
            self.assertNotIn("# My Doc", client._docs["new-doc-0"])
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_keeps_different_h1(self):
        server, client, original = self._server_and_client()
        try:
            result = server.siyuan_create({
                "notebook_id": "nb1",
                "title": "My Doc",
                "markdown": "# Other Title\n\nContent here.",
                "confirmed": True,
            })
            self.assertIn("created", result)
            self.assertIn("# Other Title", client._docs["new-doc-0"])
        finally:
            mcp_server.detect_active_profile = original

    def test_create_document_rejects_empty_after_h1_removal(self):
        server, client, original = self._server_and_client()
        try:
            with self.assertRaises(ValueError) as ctx:
                server.siyuan_create({
                    "notebook_id": "nb1",
                    "title": "My Doc",
                    "markdown": "# My Doc",
                    "confirmed": True,
                })
            self.assertIn("markdown", str(ctx.exception).casefold())
        finally:
            mcp_server.detect_active_profile = original


class BlockIdBuildTests(unittest.TestCase):
    """Tests for build_markdown_from_blocks — builds markdown directly from blocks."""

    def test_builds_markdown_with_comments(self):
        blocks = [
            {"id": "block-h1", "type": "h", "subtype": "h2", "markdown": "## My Heading", "content": "My Heading"},
            {"id": "block-p1", "type": "p", "subtype": "", "markdown": "Some paragraph text here.", "content": "Some paragraph text here."},
        ]
        result = mcp_server.build_markdown_from_blocks(blocks)
        self.assertIn("<!-- siyuan:block id=block-h1 type=h subtype=h2 -->", result)
        self.assertIn("## My Heading", result)
        self.assertIn("<!-- siyuan:block id=block-p1 type=p -->", result)
        self.assertIn("Some paragraph text here.", result)

    def test_skips_list_container_type(self):
        blocks = [
            {"id": "list-cont", "type": "l", "subtype": "u", "markdown": "* item 1\n* item 2", "content": ""},
            {"id": "item-1", "type": "i", "subtype": "u", "markdown": "* item 1", "content": ""},
        ]
        result = mcp_server.build_markdown_from_blocks(blocks)
        self.assertNotIn("list-cont", result)
        self.assertIn("item-1", result)

    def test_skips_empty_markdown(self):
        blocks = [
            {"id": "block1", "type": "p", "subtype": "", "markdown": "Visible text here.", "content": ""},
            {"id": "block2", "type": "p", "subtype": "", "markdown": "", "content": ""},
        ]
        result = mcp_server.build_markdown_from_blocks(blocks)
        self.assertIn("block1", result)
        self.assertNotIn("block2", result)

    def test_handles_empty_blocks_list(self):
        result = mcp_server.build_markdown_from_blocks([])
        self.assertEqual(result, "")

    def test_skips_document_type(self):
        blocks = [
            {"id": "doc-root", "type": "d", "subtype": "", "markdown": "root", "content": ""},
            {"id": "block-p1", "type": "p", "subtype": "", "markdown": "Body text.", "content": ""},
        ]
        result = mcp_server.build_markdown_from_blocks(blocks)
        self.assertNotIn("doc-root", result)
        self.assertIn("block-p1", result)

    def test_duplicate_text_each_gets_own_id(self):
        blocks = [
            {"id": "block-a", "type": "p", "subtype": "", "markdown": "重复文本", "content": ""},
            {"id": "block-b", "type": "p", "subtype": "", "markdown": "重复文本", "content": ""},
        ]
        result = mcp_server.build_markdown_from_blocks(blocks)
        self.assertIn("block-a", result)
        self.assertIn("block-b", result)
        self.assertEqual(result.count("<!-- siyuan:block "), 2)

    def test_tree_order_uses_parent_then_sort(self):
        blocks = [
            {"id": "a", "parent_id": "doc1", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "A", "sort": 1},
            {"id": "b", "parent_id": "doc1", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "B", "sort": 2},
            {"id": "a1", "parent_id": "a", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "A1", "sort": 1},
            {"id": "b1", "parent_id": "b", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "B1", "sort": 1},
        ]
        result = mcp_server.build_markdown_from_blocks(blocks, root_id="doc1")
        self.assertLess(result.index("id=a "), result.index("id=a1 "))
        self.assertLess(result.index("id=a1 "), result.index("id=b "))
        self.assertLess(result.index("id=b "), result.index("id=b1 "))

    def test_list_item_does_not_duplicate_child_paragraph(self):
        blocks = [
            {"id": "list", "parent_id": "doc1", "root_id": "doc1", "type": "l", "subtype": "u", "markdown": "- item", "sort": 1},
            {"id": "item", "parent_id": "list", "root_id": "doc1", "type": "i", "subtype": "u", "markdown": "- item", "sort": 1},
            {"id": "leaf", "parent_id": "item", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "item", "sort": 1},
        ]
        result = mcp_server.build_markdown_from_blocks(blocks, root_id="doc1")
        self.assertNotIn("id=list", result)
        self.assertIn("id=item", result)
        self.assertNotIn("id=leaf", result)

    def test_superblock_comment_only_then_children(self):
        blocks = [
            {"id": "super", "parent_id": "doc1", "root_id": "doc1", "type": "s", "subtype": "", "markdown": "{{{col\nA\n\n}}}", "sort": 1},
            {"id": "leaf", "parent_id": "super", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "A", "sort": 1},
        ]
        result = mcp_server.build_markdown_from_blocks(blocks, root_id="doc1")
        self.assertIn("id=super", result)
        self.assertIn("id=leaf", result)
        self.assertNotIn("{{{col", result)

    def test_child_blocks_builder_uses_api_order(self):
        class ChildClient:
            def __init__(self):
                self.children = {
                    "doc1": [
                        {"id": "b", "type": "p", "markdown": "B"},
                        {"id": "a", "type": "p", "markdown": "A"},
                    ]
                }

            def get_child_blocks(self, block_id):
                return self.children.get(block_id, [])

        result = mcp_server.build_markdown_from_child_blocks(ChildClient(), "doc1")
        self.assertLess(result.index("id=b "), result.index("id=a "))


# ── Token estimation tests ────────────────────────────────────────────

class TokenEstimationTests(unittest.TestCase):
    def test_empty_string_returns_zero(self):
        self.assertEqual(mcp_server.estimate_token_count(""), 0)

    def test_pure_cjk(self):
        tokens = mcp_server.estimate_token_count("人工智能芯片市场分析报告")
        # 10 CJK chars * 1.0 = 10
        self.assertGreater(tokens, 8)
        self.assertLessEqual(tokens, 12)

    def test_pure_english(self):
        tokens = mcp_server.estimate_token_count("The quick brown fox jumps over the lazy dog")
        # 9 words * 1.3 ≈ 11-12
        self.assertGreater(tokens, 9)
        self.assertLess(tokens, 14)

    def test_mixed_cjk_english(self):
        tokens = mcp_server.estimate_token_count("NVIDIA B300 芯片性能分析报告 2026")
        self.assertGreater(tokens, 6)
        self.assertLess(tokens, 20)

    def test_digits_count_lower(self):
        tokens = mcp_server.estimate_token_count("12345")
        # 5 digits * 0.8 = 4
        self.assertGreater(tokens, 3)
        self.assertLess(tokens, 6)

    def test_table_row(self):
        tokens = mcp_server.estimate_token_count("| 指标 | 数值 | 增长率 |")
        # some bars, some cjk, some spaces
        self.assertGreater(tokens, 3)
        self.assertLess(tokens, 15)


# ── Display block building tests ──────────────────────────────────────

class DisplayBlockBuildTests(unittest.TestCase):
    def _make_client(self, blocks_for_doc):
        class ChildClient:
            def __init__(self, blocks):
                self.blocks = blocks

            def get_child_blocks(self, block_id):
                return self.blocks.get(block_id, [])

        return ChildClient(blocks_for_doc)

    def test_builds_ordered_display_blocks(self):
        client = self._make_client({
            "doc1": [
                {"id": "h1", "type": "h", "subtype": "h2", "markdown": "## Hello"},
                {"id": "p1", "type": "p", "markdown": "World"},
            ]
        })
        blocks = mcp_server.build_display_blocks(client, "doc1")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].index, 1)
        self.assertEqual(blocks[0].id, "h1")
        self.assertTrue(blocks[0].is_heading)
        self.assertEqual(blocks[0].heading_level, 2)
        self.assertEqual(blocks[1].index, 2)
        self.assertEqual(blocks[1].id, "p1")
        self.assertFalse(blocks[1].is_heading)

    def test_renders_list_container_as_one_display_block(self):
        client = self._make_client({
            "doc1": [
                {"id": "list", "type": "l", "subtype": "u", "markdown": "- item 1\n- item 2"},
            ],
            "list": [
                {"id": "item", "type": "i", "subtype": "u", "markdown": "- item 1"},
            ]
        })
        blocks = mcp_server.build_display_blocks(client, "doc1")
        ids = [b.id for b in blocks]
        self.assertIn("list", ids)
        self.assertNotIn("item", ids)
        self.assertEqual(blocks[0].markdown, "- item 1\n- item 2")

    def test_include_block_ids_injects_comments(self):
        client = self._make_client({
            "doc1": [
                {"id": "p1", "type": "p", "markdown": "Text here."},
            ]
        })
        blocks = mcp_server.build_display_blocks(client, "doc1", include_block_ids=True)
        self.assertIn("[1] id=p1 type=paragraph", blocks[0].markdown)
        self.assertIn("Text here.", blocks[0].markdown)

    def test_no_comments_when_ids_off(self):
        client = self._make_client({
            "doc1": [
                {"id": "p1", "type": "p", "markdown": "Text here."},
            ]
        })
        blocks = mcp_server.build_display_blocks(client, "doc1", include_block_ids=False)
        self.assertEqual(blocks[0].markdown, "Text here.")

    def test_reference_reading_renders_table_coordinate_view(self):
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        client = self._make_client({
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        })
        blocks = mcp_server.build_display_blocks(client, "doc1", include_block_ids=True)
        self.assertIn("[1] id=table1 type=table rows=1 columns=2", blocks[0].markdown)
        self.assertIn("| row_index | col 1 | col 2 |", blocks[0].markdown)
        self.assertIn("| row 0 | A | B |", blocks[0].markdown)
        self.assertIn("| row 1 | 1 | 2 |", blocks[0].markdown)
        self.assertNotIn("| --- | --- |", blocks[0].markdown)
        self.assertEqual(blocks[0].source_markdown, table)

    def test_normal_reading_keeps_raw_markdown_table(self):
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        client = self._make_client({
            "doc1": [
                {"id": "table1", "type": "t", "markdown": table},
            ]
        })
        blocks = mcp_server.build_display_blocks(client, "doc1", include_block_ids=False)
        self.assertEqual(blocks[0].markdown, table)

    def test_heading_detection(self):
        client = self._make_client({
            "doc1": [
                {"id": "h1", "type": "h", "subtype": "h1", "markdown": "# Main"},
                {"id": "h2", "type": "h", "subtype": "h2", "markdown": "## Sub"},
                {"id": "h3", "type": "h", "subtype": "h3", "markdown": "### Subsub"},
            ]
        })
        blocks = mcp_server.build_display_blocks(client, "doc1")
        self.assertEqual(blocks[0].heading_level, 1)
        self.assertEqual(blocks[1].heading_level, 2)
        self.assertEqual(blocks[2].heading_level, 3)
        self.assertEqual(blocks[0].heading_text, "Main")

    def test_recursive_traversal(self):
        client = self._make_client({
            "doc1": [
                {"id": "h1", "type": "h", "subtype": "h2", "markdown": "## Section"},
            ],
            "h1": [
                {"id": "p1", "type": "p", "markdown": "Paragraph under heading."},
            ]
        })
        blocks = mcp_server.build_display_blocks(client, "doc1")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[1].id, "p1")

    def test_superblock_does_not_duplicate_child_content_in_normal_mode(self):
        client = self._make_client({
            "doc1": [
                {"id": "super", "type": "s", "markdown": "{{{col\nA\n\n}}}"},
            ],
            "super": [
                {"id": "p1", "type": "p", "markdown": "A"},
            ],
        })
        blocks = mcp_server.build_display_blocks(client, "doc1")
        self.assertEqual([b.id for b in blocks], ["p1"])
        self.assertEqual(blocks[0].markdown, "A")

    def test_estimated_tokens_set(self):
        client = self._make_client({
            "doc1": [
                {"id": "p1", "type": "p", "markdown": "Some text for token estimation."},
            ]
        })
        blocks = mcp_server.build_display_blocks(client, "doc1")
        self.assertGreater(blocks[0].estimated_tokens, 0)


# ── Block window outline tests ────────────────────────────────────────

class BlockOutlineTests(unittest.TestCase):
    def test_outline_shows_block_positions(self):
        blocks = [
            mcp_server.DisplayBlock(index=3, id="h1", type="h", subtype="h2", markdown="## Intro", estimated_tokens=5, is_heading=True, heading_level=2, heading_text="Intro"),
            mcp_server.DisplayBlock(index=7, id="h2", type="h", subtype="h3", markdown="### Detail", estimated_tokens=5, is_heading=True, heading_level=3, heading_text="Detail"),
        ]
        # Add some non-heading blocks
        blocks.insert(0, mcp_server.DisplayBlock(index=1, id="p1", type="p", subtype="", markdown="A", estimated_tokens=2))
        blocks.insert(1, mcp_server.DisplayBlock(index=2, id="p2", type="p", subtype="", markdown="B", estimated_tokens=2))
        result = mcp_server.build_block_outline(blocks)
        self.assertIn("block 3", result)
        self.assertIn("## Intro", result)
        self.assertIn("block 7", result)
        self.assertIn("### Detail", result)
        self.assertIn("2 个标题", result)

    def test_outline_no_headings(self):
        blocks = [
            mcp_server.DisplayBlock(index=1, id="p1", type="p", subtype="", markdown="Text", estimated_tokens=2),
        ]
        result = mcp_server.build_block_outline(blocks)
        self.assertIn("文档无标题结构", result)

    def test_outline_hierarchy(self):
        blocks = [
            mcp_server.DisplayBlock(index=1, id="h1", type="h", subtype="h2", markdown="## Parent", estimated_tokens=5, is_heading=True, heading_level=2, heading_text="Parent"),
            mcp_server.DisplayBlock(index=5, id="h2", type="h", subtype="h3", markdown="### Child", estimated_tokens=5, is_heading=True, heading_level=3, heading_text="Child"),
            mcp_server.DisplayBlock(index=10, id="h3", type="h", subtype="h2", markdown="## Sibling", estimated_tokens=5, is_heading=True, heading_level=2, heading_text="Sibling"),
        ]
        result = mcp_server.build_block_outline(blocks)
        # Child should be indented under Parent
        self.assertIn("block 1", result)
        self.assertIn("## Parent", result)
        self.assertIn("block 5", result)
        self.assertIn("### Child", result)
        self.assertIn("block 10", result)
        self.assertIn("## Sibling", result)


# ── Window preview tests ──────────────────────────────────────────────

class WindowPreviewTests(unittest.TestCase):
    def _make_blocks(self, count: int, with_headings: int = 0, start_hlevel: int = 2) -> list:
        blocks = []
        for i in range(1, count + 1):
            if i <= with_headings:
                htext = f"Section {i}"
                blocks.append(mcp_server.DisplayBlock(
                    index=i, id=f"h{i}", type="h", subtype=f"h{start_hlevel}",
                    markdown=f"{'#' * start_hlevel} {htext}", estimated_tokens=5,
                    is_heading=True, heading_level=start_hlevel, heading_text=htext,
                ))
            else:
                blocks.append(mcp_server.DisplayBlock(
                    index=i, id=f"p{i}", type="p", subtype="",
                    markdown=f"Paragraph number {i} with some content to fill space and test preview extraction.", estimated_tokens=10,
                ))
        return blocks

    def test_no_preview_when_enough_headings(self):
        blocks = self._make_blocks(150, with_headings=5)
        result = mcp_server.build_window_preview(blocks)
        self.assertEqual(result, "")

    def test_no_preview_when_few_blocks(self):
        blocks = self._make_blocks(50, with_headings=2)
        result = mcp_server.build_window_preview(blocks)
        self.assertEqual(result, "")

    def test_preview_when_low_headings_many_blocks(self):
        blocks = self._make_blocks(120, with_headings=3)
        result = mcp_server.build_window_preview(blocks)
        self.assertIn("标题较少", result)
        self.assertIn("block 1:", result)
        self.assertIn("block 51:", result)
        self.assertIn("block 101:", result)

    def test_preview_sampling_every_50(self):
        blocks = self._make_blocks(200, with_headings=2)
        result = mcp_server.build_window_preview(blocks)
        self.assertIn("block 1:", result)
        self.assertIn("block 51:", result)
        self.assertIn("block 101:", result)
        self.assertIn("block 151:", result)
        # Should only have 4 samples for 200 blocks
        self.assertEqual(result.count("block "), 4)

    def test_preview_unaffected_by_heading_count_equal_five(self):
        blocks = self._make_blocks(120, with_headings=5)
        result = mcp_server.build_window_preview(blocks)
        self.assertEqual(result, "")


# ── Read document integration tests (new block window path) ───────────

class McpServerReadBlockWindowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / ".test_tmp" / "mcp_blockwin"
        shutil.rmtree(self.root, ignore_errors=True)
        base = self.root / "knowledge_base"
        base.mkdir(parents=True, exist_ok=True)
        (base / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "Main"}], ensure_ascii=False),
            encoding="utf-8",
        )
        write_privacy_rules_cache(self.root, PrivacyRules(ignore=[], allow=[]))
        docs = [
            {
                "id": "doc1",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Test Doc",
                "title": "Test Doc",
                "path": "/doc1.sy",
                "tags": [],
                "word_count": 10,
                "block_count": 3,
                "updated": "20260501010101",
            },
        ]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs),
            encoding="utf-8",
        )

    def _make_client(self, blocks_for_doc=None, doc_md=None):
        class ChildFakeClient(FakeSearchClient):
            def get_child_blocks(self, block_id):
                blocks = self._blocks.get(block_id)
                if isinstance(blocks, list):
                    return blocks
                # Fallback: search across all stored blocks for matching parent_id
                children = []
                for block_list in self._blocks.values():
                    if isinstance(block_list, list):
                        children.extend(b for b in block_list if str(b.get("parent_id", "")) == block_id)
                children.sort(key=lambda b: int(b.get("sort", 0)))
                return children

        client = ChildFakeClient([])
        client._hpaths["doc1"] = "/Test Doc"
        if blocks_for_doc:
            client._blocks = blocks_for_doc
        client._docs["doc1"] = doc_md or "## Section\n\nBody text here.\n"
        return client

    def _read(self, args: dict[str, Any], blocks_for_doc=None, doc_md=None):
        client = self._make_client(blocks_for_doc, doc_md=doc_md)
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        return server.siyuan_read(args)

    def test_default_block_window_mode(self):
        blocks = {
            "doc1": [
                {"id": "h1", "parent_id": "doc1", "type": "h", "subtype": "h2", "markdown": "## Section", "sort": 1},
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "Body text here.", "sort": 2},
            ]
        }
        result = self._read({"document_id": "doc1"}, blocks_for_doc=blocks)
        self.assertIn("普通阅读", result)
        self.assertIn("展示块：", result)
        self.assertIn("更新：2026-05-01", result)
        self.assertIn("估算令牌数：", result)
        self.assertIn("## Section", result)
        self.assertIn("Body text here.", result)
        # Should NOT contain old chunk header
        self.assertNotIn("Chunk ", result)

    def test_read_header_shows_document_tags(self):
        base = self.root / "knowledge_base"
        docs = [
            {
                "id": "doc1",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Test Doc",
                "title": "Test Doc",
                "path": "/doc1.sy",
                "tags": ["参考", "商业"],
                "word_count": 10,
                "block_count": 3,
                "updated": "20260501010101",
            },
        ]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs),
            encoding="utf-8",
        )
        blocks = {
            "doc1": [
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "Body text here.", "sort": 1},
            ]
        }
        result = self._read({"document_id": "doc1"}, blocks_for_doc=blocks)

        self.assertIn("tag：#参考# #商业#", result)
        self.assertGreater(result.index("tag：#参考# #商业#"), result.index("更新：2026-05-01"))
        self.assertLess(result.index("tag：#参考# #商业#"), result.index("阅读模式："))

    def test_read_accepts_document_path(self):
        blocks = {
            "doc1": [
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "Body text here.", "sort": 1},
            ]
        }
        result = self._read({"document": "/Main/Test Doc"}, blocks_for_doc=blocks)
        self.assertIn("# 文档：/Main/Test Doc", result)
        self.assertIn("Body text here.", result)

    def test_read_rewrites_asset_links_to_absolute_paths(self):
        blocks = {
            "doc1": [
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "![chart](assets/chart.png)", "sort": 1},
            ]
        }
        result = self._read(
            {"document_id": "doc1"},
            blocks_for_doc=blocks,
            doc_md="![chart](assets/chart.png)",
        )
        expected_path = (self.root / "ai_workspace" / "attachments" / "doc1" / "assets" / "chart.png").resolve().as_posix()
        expected_dir = (self.root / "ai_workspace" / "attachments" / "doc1").resolve()
        self.assertIn(f"![chart]({expected_path})", result)
        self.assertIn(str(expected_dir), result)
        self.assertNotIn("](assets/chart.png)", result)

    def test_block_window_header_shows_range(self):
        blocks = {
            "doc1": [
                {"id": "h1", "parent_id": "doc1", "type": "h", "subtype": "h2", "markdown": "## Section", "sort": 1},
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "Body text here.", "sort": 2},
            ]
        }
        result = self._read({"document_id": "doc1"}, blocks_for_doc=blocks)
        self.assertIn("展示块：1-2 / 2", result)

    def test_block_start_pagination(self):
        blocks = {
            "doc1": [
                {"id": "h1", "parent_id": "doc1", "type": "h", "subtype": "h2", "markdown": "## First", "sort": 1},
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "First paragraph.", "sort": 2},
                {"id": "h2", "parent_id": "doc1", "type": "h", "subtype": "h2", "markdown": "## Second", "sort": 3},
                {"id": "p2", "parent_id": "doc1", "type": "p", "markdown": "Second paragraph.", "sort": 4},
            ]
        }
        result = self._read({"document_id": "doc1", "block_start": 3}, blocks_for_doc=blocks)
        self.assertIn("展示块：3-4 / 4", result)
        # Body (after last ---) should contain Second but not First
        body_start = result.rindex("---")
        body = result[body_start:]
        self.assertIn("## Second", body)
        self.assertIn("Second paragraph.", body)
        self.assertNotIn("## First", body)
        # Outline (above body) still shows all headings
        self.assertIn("block 1: ## First", result)

    def test_block_limit_restricts_window(self):
        blocks = {}
        blocks["doc1"] = []
        for i in range(10):
            blocks["doc1"].append({
                "id": f"p{i}", "parent_id": "doc1", "type": "p",
                "markdown": f"Paragraph {i}.", "sort": i,
            })
        result = self._read({"document_id": "doc1", "block_limit": 3}, blocks_for_doc=blocks)
        self.assertIn("展示块：1-3 / 10", result)
        self.assertIn("Paragraph 0.", result)
        self.assertIn("Paragraph 2.", result)
        self.assertNotIn("Paragraph 3.", result)

    def test_token_budget_stops_at_block_boundary(self):
        blocks = {
            "doc1": [
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "Short.", "sort": 1},
                {"id": "p2", "parent_id": "doc1", "type": "p", "markdown": "Another.", "sort": 2},
                {"id": "p3", "parent_id": "doc1", "type": "p", "markdown": "A" + "x" * 500 + " really long paragraph that would blow budget.", "sort": 3},
            ]
        }
        # Very small budget should return at least block 1
        result = self._read({"document_id": "doc1", "token_budget": 10}, blocks_for_doc=blocks)
        self.assertIn("Short.", result)
        self.assertIn("估算令牌数：", result)
        # At least one block returned
        self.assertIn("Short.", result)

    def test_default_token_budget_stays_within_host_output_limit(self):
        spec = next(tool for tool in mcp_server.tool_specs() if tool["name"] == "siyuan_read")
        self.assertEqual(spec["inputSchema"]["properties"]["token_budget"]["default"], 10000)
        self.assertEqual(mcp_server.DEFAULT_TOKEN_BUDGET, 10000)

    def test_next_window_hint(self):
        blocks = {}
        blocks["doc1"] = []
        for i in range(10):
            blocks["doc1"].append({
                "id": f"p{i}", "parent_id": "doc1", "type": "p",
                "markdown": f"Paragraph {i}.", "sort": i,
            })
        result = self._read({"document_id": "doc1", "block_limit": 5}, blocks_for_doc=blocks)
        self.assertIn("下一窗口：", result)
        self.assertIn("block_start=6", result)

    def test_include_block_ids_is_reference_reading(self):
        blocks = {
            "doc1": [
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "Hello world.", "sort": 1},
            ]
        }
        result = self._read({"document_id": "doc1", "include_block_ids": True}, blocks_for_doc=blocks)
        self.assertIn("引用阅读", result)
        self.assertIn("[1] id=p1 type=paragraph", result)

    def test_window_preview_integration(self):
        blocks = {}
        blocks["doc1"] = []
        for i in range(1, 121):
            blocks["doc1"].append({
                "id": f"p{i}", "parent_id": "doc1", "type": "p",
                "markdown": f"Paragraph number {i} content here.", "sort": i,
            })
        result = self._read({"document_id": "doc1", "block_limit": 200, "token_budget": 200000}, blocks_for_doc=blocks)
        # 0 headings, 120 blocks → should show window preview
        self.assertIn("标题较少（0 个）", result)
        self.assertIn("block 1:", result)
        self.assertIn("block 51:", result)
        self.assertIn("block 101:", result)

    def test_no_window_preview_with_headings(self):
        blocks = {}
        blocks["doc1"] = []
        # 5 headings, 120 blocks → no preview
        for i in range(1, 121):
            if i <= 5:
                blocks["doc1"].append({
                    "id": f"h{i}", "parent_id": "doc1", "type": "h", "subtype": "h2",
                    "markdown": f"## Heading {i}", "sort": i,
                })
            else:
                blocks["doc1"].append({
                    "id": f"p{i}", "parent_id": "doc1", "type": "p",
                    "markdown": f"Paragraph {i}.", "sort": i,
                })
        result = self._read({"document_id": "doc1", "block_limit": 200, "token_budget": 200000}, blocks_for_doc=blocks)
        self.assertNotIn("标题较少", result)
        self.assertIn("大纲", result)

    def test_outline_shows_block_positions(self):
        blocks = {
            "doc1": [
                {"id": "h1", "parent_id": "doc1", "type": "h", "subtype": "h2", "markdown": "## Section One", "sort": 1},
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "Body paragraph.", "sort": 2},
            ]
        }
        result = self._read({"document_id": "doc1"}, blocks_for_doc=blocks)
        self.assertIn("block 1:", result)
        self.assertIn("## Section One", result)

class McpServerReadBlockIdTests(unittest.TestCase):
    """Integration tests for reference reading with include_block_ids."""

    def setUp(self):
        self.root = Path.cwd() / ".test_tmp" / "mcp_blockid"
        shutil.rmtree(self.root, ignore_errors=True)
        base = self.root / "knowledge_base"
        base.mkdir(parents=True, exist_ok=True)
        (base / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "Main"}], ensure_ascii=False),
            encoding="utf-8",
        )
        write_privacy_rules_cache(self.root, PrivacyRules(ignore=[], allow=[]))
        self.doc_md = "## Section One\n\nBody paragraph here.\n\nAnother paragraph.\n"
        docs = [
            {
                "id": "doc1",
                "notebook_id": "nb1",
                "notebook_name": "Main",
                "hpath": "/Test Doc",
                "title": "Test Doc",
                "path": "/doc1.sy",
                "tags": [],
                "word_count": 10,
                "block_count": 3,
                "updated": "20260501010101",
            },
        ]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs),
            encoding="utf-8",
        )

    def _make_client(self, blocks_for_doc=None):
        class ChildFakeClient(FakeSearchClient):
            def get_child_blocks(self, block_id):
                blocks = self._blocks.get(block_id)
                if isinstance(blocks, list):
                    return blocks
                children = []
                for block_list in self._blocks.values():
                    if isinstance(block_list, list):
                        children.extend(b for b in block_list if str(b.get("parent_id", "")) == block_id)
                children.sort(key=lambda b: int(b.get("sort", 0)))
                return children

        client = ChildFakeClient([])
        if blocks_for_doc:
            client._blocks = blocks_for_doc
        client._docs["doc1"] = self.doc_md
        return client

    def test_default_excludes_block_ids(self):
        blocks = {
            "doc1": [
                {"id": "h1", "parent_id": "doc1", "type": "h", "subtype": "h2", "markdown": "## Section One", "sort": 1},
                {"id": "p1", "parent_id": "doc1", "type": "p", "markdown": "Body paragraph here.", "sort": 2},
            ]
        }
        client = self._make_client(blocks_for_doc=blocks)
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        result = server.siyuan_read({"document_id": "doc1"})
        self.assertNotIn("<!-- siyuan:block", result)
        self.assertIn("普通阅读", result)
        self.assertIn("## Section One", result)
        self.assertIn("Body paragraph here.", result)

    def test_include_block_ids_builds_reference_view(self):
        blocks = {
            "doc1": [
                {"id": "block-h1", "parent_id": "doc1", "type": "h", "subtype": "h2", "markdown": "## Section One", "sort": 1},
                {"id": "block-p1", "parent_id": "doc1", "type": "p", "markdown": "Body paragraph here.", "sort": 2},
            ]
        }
        client = self._make_client(blocks_for_doc=blocks)
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        result = server.siyuan_read({"document_id": "doc1", "include_block_ids": True})
        self.assertIn("[1] id=block-h1 type=heading", result)
        self.assertIn("## Section One", result)
        self.assertIn("[2] id=block-p1 type=paragraph", result)
        self.assertIn("Body paragraph here.", result)
        self.assertIn("引用阅读", result)

    def test_include_block_ids_preserves_outline(self):
        blocks = {
            "doc1": [
                {"id": "block-h1", "parent_id": "doc1", "type": "h", "subtype": "h2", "markdown": "## Section One", "sort": 1},
                {"id": "block-p1", "parent_id": "doc1", "type": "p", "markdown": "Body paragraph here.", "sort": 2},
                {"id": "block-p2", "parent_id": "doc1", "type": "p", "markdown": "Another paragraph.", "sort": 3},
            ]
        }
        client = self._make_client(blocks_for_doc=blocks)
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        result = server.siyuan_read({"document_id": "doc1", "include_block_ids": True})
        self.assertIn("[1] id=block-h1 type=heading", result)
        self.assertIn("大纲", result)
        self.assertIn("Section One", result)


class EmbedReadFakeClient(FakeSearchClient):
    def __init__(
        self,
        *,
        result_ids: list[str] | None = None,
        metadata_rows: list[dict[str, Any]] | None = None,
        content_rows: list[dict[str, Any]] | None = None,
        result_map: list[tuple[str, list[str]]] | None = None,
        asset_bytes: bytes = b"asset-bytes",
    ):
        super().__init__([])
        self.result_ids = list(result_ids or [])
        self.metadata_rows = list(metadata_rows or [])
        self.content_rows = list(content_rows or [])
        self.result_map = list(result_map or [])
        self.asset_bytes = asset_bytes
        self.sql_calls: list[str] = []
        self.content_queries: list[str] = []
        self.fail_embed_query = False
        self.asset_requests: list[str] = []

    def query_sql(self, statement):
        text = str(statement or "")
        normalized = text.casefold()
        self.sql_calls.append(text)
        if normalized.startswith("select id from (") and "as embed_matches" in normalized:
            if self.fail_embed_query:
                raise RuntimeError("simulated embed resolution failure")
            for marker, ids in self.result_map:
                if marker.casefold() in normalized:
                    return [{"id": block_id} for block_id in ids]
            return [{"id": block_id} for block_id in self.result_ids]
        if normalized.startswith("select id, root_id, box, hpath, path, type, subtype"):
            return [
                row for row in self.metadata_rows
                if f"'{row.get('id')}" in text
            ]
        if normalized.startswith("select id, markdown, content from blocks"):
            self.content_queries.append(text)
            return [
                row for row in self.content_rows
                if f"'{row.get('id')}" in text
            ]
        return super().query_sql(statement)

    def get_asset(self, asset_path):
        self.asset_requests.append(str(asset_path))
        return self.asset_bytes


class McpServerReadEmbeddedBlocksTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / ".test_tmp" / "mcp_embed_read"
        shutil.rmtree(self.root, ignore_errors=True)
        base = self.root / "knowledge_base"
        base.mkdir(parents=True, exist_ok=True)
        (base / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "Main"}], ensure_ascii=False),
            encoding="utf-8",
        )
        self.docs = [
            self._doc("host", "/Host"),
            self._doc("source-doc", "/Source"),
            self._doc("source-b", "/Source B"),
            *(self._doc(f"source-{index}", f"/Source {index}") for index in range(1, 9)),
            self._doc("secret-doc", "/Secret"),
        ]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in self.docs),
            encoding="utf-8",
        )
        write_privacy_rules_cache(
            self.root,
            PrivacyRules(ignore=[{"scope": "document", "id": "secret-doc"}], allow=[]),
        )

    @staticmethod
    def _doc(doc_id: str, hpath: str) -> dict[str, Any]:
        return {
            "id": doc_id,
            "notebook_id": "nb1",
            "notebook_name": "Main",
            "hpath": hpath,
            "title": hpath.rsplit("/", 1)[-1],
            "path": f"/{doc_id}.sy",
            "tags": [],
            "word_count": 0,
            "block_count": 1,
            "updated": "20260501010101",
        }

    @staticmethod
    def _meta(block_id: str, doc_id: str, block_type: str = "p", *, subtype: str = ""):
        doc_paths = {
            "host": "/Host",
            "source-doc": "/Source",
            "source-b": "/Source B",
            "secret-doc": "/Secret",
        }
        return {
            "id": block_id,
            "root_id": doc_id,
            "box": "nb1",
            "hpath": doc_paths.get(doc_id, f"/{doc_id}"),
            "path": f"/{doc_id}.sy",
            "type": block_type,
            "subtype": subtype,
        }

    @staticmethod
    def _embed(block_id: str = "embed-1", sql: str = "SELECT id FROM blocks WHERE root_id='source-doc'"):
        return {
            "id": block_id,
            "parent_id": "host",
            "root_id": "host",
            "type": "query_embed",
            "markdown": "{{" + sql + "}}",
            "sort": 1,
        }

    def _make_client(
        self,
        host_blocks: list[dict[str, Any]],
        *,
        result_ids: list[str] | None = None,
        metadata_rows: list[dict[str, Any]] | None = None,
        content_rows: list[dict[str, Any]] | None = None,
        result_map: list[tuple[str, list[str]]] | None = None,
        blocks_by_root: dict[str, list[dict[str, Any]]] | None = None,
        asset_bytes: bytes = b"asset-bytes",
    ) -> EmbedReadFakeClient:
        client = EmbedReadFakeClient(
            result_ids=result_ids,
            metadata_rows=metadata_rows,
            content_rows=content_rows,
            result_map=result_map,
            asset_bytes=asset_bytes,
        )
        client._blocks = {"host": host_blocks, **(blocks_by_root or {})}
        client._hpaths.update({doc["id"]: doc["hpath"] for doc in self.docs})
        client._docs["host"] = "Host body"
        return client

    def _read(self, client: EmbedReadFakeClient, **args):
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        result = server.siyuan_read({"document_id": "host", **args})
        return server, result

    def test_visible_results_preserve_sql_order_and_never_read_hidden_content(self):
        embed = self._embed()
        metadata = [
            self._meta("visible-a", "source-doc"),
            self._meta("secret-block", "secret-doc"),
            self._meta("visible-b", "source-b"),
        ]
        content = [
            {"id": "visible-a", "markdown": "Alpha visible text", "content": "Alpha visible text"},
            {"id": "secret-block", "markdown": "SECRET_CONTENT", "content": "SECRET_CONTENT"},
            {"id": "visible-b", "markdown": "Beta visible text", "content": "Beta visible text"},
        ]
        client = self._make_client(
            [embed],
            result_ids=["visible-a", "secret-block", "visible-b"],
            metadata_rows=metadata,
            content_rows=content,
        )

        _, result = self._read(client, include_block_ids=True)

        self.assertIn("[1] id=embed-1 type=query_embed", result)
        self.assertIn("嵌入块来源：/Main/Source source-doc visible-a", result)
        self.assertIn("嵌入块来源：/Main/Source B source-b visible-b", result)
        self.assertLess(result.index("> Alpha visible text"), result.index("> Beta visible text"))
        self.assertIn("嵌入内容共2个块，当前展示2个块", result)
        self.assertNotIn("secret-doc", result)
        self.assertNotIn("secret-block", result)
        self.assertNotIn("SECRET_CONTENT", result)
        self.assertEqual(
            {"visible-a", "visible-b"},
            {block_id for block_id in ("visible-a", "visible-b") if any(f"'{block_id}'" in query for query in client.content_queries)},
        )
        self.assertTrue(all("secret-block" not in query for query in client.content_queries))

    def test_all_hidden_results_are_reported_only_as_missing(self):
        embed = self._embed()
        client = self._make_client(
            [embed],
            result_ids=["secret-block"],
            metadata_rows=[self._meta("secret-block", "secret-doc")],
            content_rows=[{"id": "secret-block", "markdown": "SECRET_CONTENT", "content": "SECRET_CONTENT"}],
        )

        _, result = self._read(client)

        self.assertIn("嵌入块来源：查无此块", result)
        self.assertIn("> 查无此块", result)
        self.assertIn("嵌入内容共0个块，当前展示0个块", result)
        self.assertNotIn("secret-doc", result)
        self.assertNotIn("secret-block", result)
        self.assertNotIn("SECRET_CONTENT", result)
        self.assertTrue(all("secret-block" not in query for query in client.content_queries))

    def test_target_missing_between_metadata_and_content_reads_is_not_disclosed(self):
        embed = self._embed(sql="SELECT id FROM blocks WHERE type='p'")
        client = self._make_client(
            [embed],
            result_ids=["missing-block"],
            metadata_rows=[self._meta("missing-block", "source-doc")],
            content_rows=[],
        )

        _, result = self._read(client)

        self.assertIn("嵌入块来源：查无此块", result)
        self.assertIn("嵌入内容共0个块，当前展示0个块", result)
        self.assertNotIn("source-doc", result)
        self.assertNotIn("missing-block", result)

    def test_document_target_uses_its_own_attachment_directory(self):
        embed = self._embed()
        client = self._make_client(
            [embed],
            result_ids=["source-doc"],
            metadata_rows=[self._meta("source-doc", "source-doc", "d")],
            content_rows=[{"id": "source-doc", "markdown": "", "content": ""}],
            blocks_by_root={
                "source-doc": [
                    {
                        "id": "source-image",
                        "parent_id": "source-doc",
                        "root_id": "source-doc",
                        "type": "p",
                        "markdown": "![Diagram](assets/diagram.png)\nSource paragraph",
                        "sort": 1,
                    }
                ]
            },
            asset_bytes=b"\x89PNG\r\nembedded-image",
        )

        _, result = self._read(client)

        target_asset = mcp_server.attachment_root_dir(self.root, "source-doc") / "assets" / "diagram.png"
        self.assertTrue(target_asset.exists())
        self.assertEqual(["assets/diagram.png"], client.asset_requests)
        self.assertIn("嵌入块来源：/Main/Source source-doc", result)
        self.assertNotIn("source-image", result)
        self.assertIn(target_asset.resolve().as_posix(), result)
        self.assertIn("> Source paragraph", result)
        self.assertIn("嵌入内容共1个块，当前展示1个块", result)

    def test_heading_target_includes_heading_and_its_children(self):
        embed = self._embed()
        client = self._make_client(
            [embed],
            result_ids=["heading-1"],
            metadata_rows=[self._meta("heading-1", "source-doc", "h", subtype="h2")],
            content_rows=[{"id": "heading-1", "markdown": "## Section", "content": "Section"}],
            blocks_by_root={
                "heading-1": [
                    {
                        "id": "heading-child",
                        "parent_id": "heading-1",
                        "root_id": "source-doc",
                        "type": "p",
                        "markdown": "Inside section",
                        "sort": 1,
                    }
                ]
            },
        )

        _, result = self._read(client)

        self.assertIn("> ## Section", result)
        self.assertIn("> Inside section", result)
        self.assertIn("嵌入内容共2个块，当前展示2个块", result)

    def test_more_than_twenty_sql_matches_are_capped_after_visibility_filtering(self):
        embed = self._embed()
        ids = [f"match-{i}" for i in range(25)]
        metadata = [self._meta(block_id, "source-doc") for block_id in ids]
        content = [
            {"id": block_id, "markdown": f"Content {i}", "content": f"Content {i}"}
            for i, block_id in enumerate(ids)
        ]
        client = self._make_client(
            [embed],
            result_ids=ids,
            metadata_rows=metadata,
            content_rows=content,
        )

        _, result = self._read(client)

        self.assertIn("嵌入内容共25个块，当前展示20个块", result)
        self.assertIn("match-19", result)
        self.assertNotIn("match-20", result)
        self.assertTrue(all("'match-20'" not in query for query in client.content_queries))

    def test_embed_resolution_error_returns_sql_fallback_instead_of_failing_read(self):
        embed = self._embed(sql="SELECT id FROM blocks WHERE id='missing-target'")
        client = self._make_client([embed])
        client.fail_embed_query = True

        _, result = self._read(client)

        self.assertIn("{{SELECT id FROM blocks WHERE id='missing-target'}}", result)
        self.assertIn("嵌入块内容：内容超预算不展开", result)
        self.assertIn("嵌入内容共0个块，当前展示0个块", result)

    def test_non_select_embed_stays_as_original_sql(self):
        embed = self._embed(sql="UPDATE blocks SET content='changed'")
        client = self._make_client([embed])

        _, result = self._read(client)

        self.assertIn("{{UPDATE blocks SET content='changed'}}", result)
        self.assertNotIn("嵌入块来源：", result)
        self.assertFalse(any("as embed_matches" in query.casefold() for query in client.sql_calls))

    def test_oversized_embed_stops_contiguous_window_then_falls_back_alone(self):
        embed = self._embed(sql="SELECT id FROM blocks WHERE id='heavy-block'")
        host_blocks = [
            {"id": "before", "parent_id": "host", "root_id": "host", "type": "p", "markdown": "before " * 900, "sort": 1},
            {**embed, "sort": 2},
            {"id": "after", "parent_id": "host", "root_id": "host", "type": "p", "markdown": "AFTER_WINDOW_BLOCK", "sort": 3},
        ]
        client = self._make_client(
            host_blocks,
            result_ids=["heavy-block"],
            metadata_rows=[self._meta("heavy-block", "source-doc")],
            content_rows=[{"id": "heavy-block", "markdown": "UNIQUE_SECRET_EMBED " * 1200, "content": ""}],
        )

        _, first = self._read(client, block_limit=3, token_budget=1000)
        _, second = self._read(client, block_start=2, block_limit=3, token_budget=1000)
        _, third = self._read(client, block_start=3, block_limit=3, token_budget=1000)

        self.assertIn("block_start=2", first)
        self.assertNotIn("嵌入块来源：", first)
        self.assertIn("嵌入块内容：内容超预算不展开", second)
        self.assertIn("当前展示0个块", second)
        self.assertNotIn("UNIQUE_SECRET_EMBED", second)
        self.assertIn("block_start=3", second)
        self.assertIn("AFTER_WINDOW_BLOCK", third)

    def test_image_budget_failure_falls_back_without_partial_embed_content(self):
        embed = self._embed(sql="SELECT id FROM blocks WHERE id='image-block'")
        client = self._make_client(
            [embed],
            result_ids=["image-block"],
            metadata_rows=[self._meta("image-block", "source-doc")],
            content_rows=[{
                "id": "image-block",
                "markdown": "![Photo](assets/photo.png)\nUNIQUE_IMAGE_EMBED_TEXT",
                "content": "",
            }],
            asset_bytes=b"\x89PNG\r\n" + b"x" * 32,
        )

        with mock.patch.object(
            mcp_server,
            "load_config",
            return_value=mock.Mock(read_inline_images=True, inline_image_budget_bytes=None),
        ):
            with mock.patch.object(mcp_server, "INLINE_RESPONSE_BUDGET_BYTES", 4):
                _, result = self._read(client, token_budget=10000)

        self.assertIn("嵌入块内容：内容超预算不展开", result)
        self.assertNotIn("UNIQUE_IMAGE_EMBED_TEXT", result)
        self.assertNotIn("@@SIYUAN-IMAGE:", result)

    def test_recursive_embed_stops_at_depth_seven_before_reading_next_target(self):
        host_embed = self._embed("embed-0", "SELECT id FROM blocks WHERE id='source-1'")
        blocks_by_root: dict[str, list[dict[str, Any]]] = {}
        metadata = [self._meta("host", "host", "d")]
        content = [{"id": "host", "markdown": "", "content": ""}]
        result_map: list[tuple[str, list[str]]] = []
        for index in range(1, 9):
            doc_id = f"source-{index}"
            metadata.append(self._meta(doc_id, doc_id, "d"))
            content.append({"id": doc_id, "markdown": "", "content": ""})
            if index < 8:
                next_id = f"source-{index + 1}"
                result_map.append((f"id='{next_id}'", [next_id]))
                blocks_by_root[doc_id] = [
                    {
                        "id": f"embed-{index}",
                        "parent_id": doc_id,
                        "root_id": doc_id,
                        "type": "query_embed",
                        "markdown": f"{{{{SELECT id FROM blocks WHERE id='{next_id}'}}}}",
                        "sort": 1,
                    }
                ]
        result_map.insert(0, ("id='source-1'", ["source-1"]))
        client = self._make_client(
            [host_embed],
            metadata_rows=metadata,
            content_rows=content,
            result_map=result_map,
            blocks_by_root=blocks_by_root,
        )

        _, result = self._read(client)

        self.assertIn("嵌入层级超过7层，停止解析", result)
        self.assertNotIn("source-8'", " ".join(client.content_queries))

    def test_recursive_cycle_stops_at_repeated_embed_block(self):
        first_embed = self._embed("embed-a", "SELECT id FROM blocks WHERE id='source-doc'")
        second_embed = {
            "id": "embed-b",
            "parent_id": "source-doc",
            "root_id": "source-doc",
            "type": "query_embed",
            "markdown": "{{SELECT id FROM blocks WHERE id='host'}}",
            "sort": 1,
        }
        client = self._make_client(
            [first_embed],
            metadata_rows=[
                self._meta("source-doc", "source-doc", "d"),
                self._meta("host", "host", "d"),
            ],
            content_rows=[
                {"id": "source-doc", "markdown": "", "content": ""},
                {"id": "host", "markdown": "", "content": ""},
            ],
            result_map=[
                ("id='source-doc'", ["source-doc"]),
                ("id='host'", ["host"]),
            ],
            blocks_by_root={"source-doc": [second_embed]},
        )

        _, result = self._read(client)

        self.assertIn("循环嵌套，停止解析", result)
        self.assertIn("嵌入内容共0个块，当前展示0个块", result)
        self.assertLessEqual(result.count("循环嵌套，停止解析"), 1)


class McpServerReadInlineImagesTests(unittest.TestCase):
    """读文档时内联返回图片（docs/图片内联需求-2026-09-14.md 已定决策）。"""

    def setUp(self):
        self.root = Path.cwd() / ".test_tmp" / "mcp_inline_img"
        shutil.rmtree(self.root, ignore_errors=True)
        base = self.root / "knowledge_base"
        base.mkdir(parents=True, exist_ok=True)
        (base / "notebooks.json").write_text(
            json.dumps([{"id": "nb1", "name": "Main"}], ensure_ascii=False),
            encoding="utf-8",
        )
        write_privacy_rules_cache(self.root, PrivacyRules(ignore=[], allow=[]))
        docs = [{
            "id": "doc1", "notebook_id": "nb1", "notebook_name": "Main",
            "hpath": "/Test Doc", "title": "Test Doc", "path": "/doc1.sy",
            "tags": [], "word_count": 10, "block_count": 3, "updated": "20260501010101",
        }]
        (base / "docs.jsonl").write_text(
            "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs),
            encoding="utf-8",
        )
        self.png_bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 48
        self.write_config({"profiles": [{"name": "t", "token": "t"}], "read_inline_images": True})

    def write_config(self, data: dict[str, Any]) -> None:
        (self.root / "config.local.json").write_text(json.dumps(data), encoding="utf-8")

    def _blocks(self, items: list[tuple[str, str]]) -> dict[str, list[dict[str, Any]]]:
        return {
            "doc1": [
                {"id": bid, "parent_id": "doc1", "type": "p", "markdown": md, "sort": sort}
                for sort, (bid, md) in enumerate(items, start=1)
            ]
        }

    def _make_server(self, blocks: dict[str, list[dict[str, Any]]], doc_md: str):
        class InlineFakeClient(FakeSearchClient):
            def __init__(self, bytes_payload: bytes):
                super().__init__([])
                self._bytes_payload = bytes_payload
                self.asset_requests: list[str] = []

            def get_child_blocks(self, block_id):
                stored = self._blocks.get(block_id)
                if isinstance(stored, list):
                    return stored
                children = []
                for block_list in self._blocks.values():
                    if isinstance(block_list, list):
                        children.extend(b for b in block_list if str(b.get("parent_id", "")) == block_id)
                children.sort(key=lambda b: int(b.get("sort", 0)))
                return children

            def get_asset(self, asset_path):
                self.asset_requests.append(asset_path)
                return self._bytes_payload

        client = InlineFakeClient(self.png_bytes)
        client._hpaths["doc1"] = "/Test Doc"
        client._blocks = blocks
        client._docs["doc1"] = doc_md
        server = mcp_server.McpServer(self.root)
        server._active_profile = Profile(name="test", token="test")
        server._active_client = client
        return server

    def _text(self, content: list[dict[str, Any]]) -> str:
        return "\n".join(item["text"] for item in content if item["type"] == "text")

    def test_toggle_off_keeps_plain_text_read(self):
        self.write_config({"profiles": [{"name": "t", "token": "t"}]})
        blocks = self._blocks([("p1", "![chart](assets/chart.png)")])
        server = self._make_server(blocks, "![chart](assets/chart.png)")
        result = server.siyuan_read({"document_id": "doc1"})
        self.assertIsInstance(result, str)
        self.assertNotIn("@@SIYUAN-IMAGE:", result)
        self.assertIsNone(getattr(server, "_pending_read_images", None))
        expected = (self.root / "ai_workspace" / "attachments" / "doc1" / "assets" / "chart.png").resolve().as_posix()
        self.assertIn(f"![chart]({expected})", result)

    def test_toggle_on_inlines_local_asset_into_content_array(self):
        blocks = self._blocks([("p1", "![chart](assets/chart.png)"), ("p2", "After the image.")])
        server = self._make_server(blocks, "![chart](assets/chart.png)")
        response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        content = response["result"]["content"]
        image_items = [item for item in content if item["type"] == "image"]
        self.assertEqual(len(image_items), 1)
        self.assertEqual(image_items[0]["mimeType"], "image/png")
        self.assertEqual(
            mcp_server.base64.b64decode(image_items[0]["data"]),
            self.png_bytes,
        )
        text = self._text(content)
        expected = (self.root / "ai_workspace" / "attachments" / "doc1" / "assets" / "chart.png").resolve().as_posix()
        self.assertIn(f"[图片已内联：{expected}]", text)
        self.assertNotIn("@@SIYUAN-IMAGE:", text)

    def test_unsupported_format_is_declared_in_place(self):
        blocks = self._blocks([("p1", "![logo](assets/logo.svg)")])
        server = self._make_server(blocks, "![logo](assets/logo.svg)")
        response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        content = response["result"]["content"]
        self.assertTrue(all(item["type"] == "text" for item in content))
        self.assertIn("[图片未返回：格式 .svg 平台通常不支持内联", self._text(content))
        self.assertIn("assets/logo.svg", self._text(content))

    def test_oversized_image_declared_without_confirm(self):
        blocks = self._blocks([("p1", "![chart](assets/chart.png)")])
        server = self._make_server(blocks, "![chart](assets/chart.png)")
        with mock.patch.object(mcp_server, "INLINE_RESPONSE_BUDGET_BYTES", 10):
            response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        content = response["result"]["content"]
        self.assertTrue(all(item["type"] == "text" for item in content))
        self.assertIn("[图片未返回：超出单笔", self._text(content))
        self.assertIn("include_large_images=true", self._text(content))
        self.assertIn("chart.png", self._text(content))

    def test_config_image_budget_overrides_builtin_default(self):
        blocks = self._blocks([("p1", "![chart](assets/chart.png)")])
        server = self._make_server(blocks, "![chart](assets/chart.png)")
        self.write_config({
            "profiles": [{"name": "t", "token": "t"}],
            "read_inline_images": True,
            "inline_image_budget_mb": 0,
        })
        response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        content = response["result"]["content"]
        self.assertTrue(all(item["type"] == "text" for item in content))
        self.assertIn("[图片未返回：超出单笔", self._text(content))
        self.assertIn("0 字节", self._text(content))

    def test_include_images_false_overrides_enabled_setting(self):
        blocks = self._blocks([("p1", "![chart](assets/chart.png)"), ("p2", "After the image.")])
        server = self._make_server(blocks, "![chart](assets/chart.png)")
        response = server.call_tool(1, "siyuan_read", {"document_id": "doc1", "include_images": False})
        content = response["result"]["content"]
        self.assertTrue(all(item["type"] == "text" for item in content))
        self.assertNotIn("@@SIYUAN-IMAGE:", self._text(content))
        expected = (self.root / "ai_workspace" / "attachments" / "doc1" / "assets" / "chart.png").resolve().as_posix()
        self.assertIn(f"![chart]({expected})", self._text(content))

    def test_include_images_true_overrides_disabled_setting(self):
        blocks = self._blocks([("p1", "![chart](assets/chart.png)")])
        server = self._make_server(blocks, "![chart](assets/chart.png)")
        self.write_config({"profiles": [{"name": "t", "token": "t"}], "read_inline_images": False})
        response = server.call_tool(1, "siyuan_read", {"document_id": "doc1", "include_images": True})
        image_items = [item for item in response["result"]["content"] if item["type"] == "image"]
        self.assertEqual(len(image_items), 1)
        self.assertEqual(image_items[0]["mimeType"], "image/png")

    def test_include_images_false_blocks_include_large_images(self):
        blocks = self._blocks([("p1", "![chart](assets/chart.png)")])
        server = self._make_server(blocks, "![chart](assets/chart.png)")
        response = server.call_tool(
            1,
            "siyuan_read",
            {"document_id": "doc1", "include_images": False, "include_large_images": True},
        )
        content = response["result"]["content"]
        self.assertTrue(all(item["type"] == "text" for item in content))
        self.assertNotIn("@@SIYUAN-IMAGE:", self._text(content))

    def test_oversized_image_inlined_after_confirm(self):
        blocks = self._blocks([("p1", "![chart](assets/chart.png)")])
        server = self._make_server(blocks, "![chart](assets/chart.png)")
        with mock.patch.object(mcp_server, "INLINE_RESPONSE_BUDGET_BYTES", 10):
            response = server.call_tool(
                1, "siyuan_read", {"document_id": "doc1", "include_large_images": True}
            )
        image_items = [item for item in response["result"]["content"] if item["type"] == "image"]
        self.assertEqual(len(image_items), 1)
        self.assertEqual(image_items[0]["mimeType"], "image/png")

    def test_network_image_inlined_with_declared_source(self):
        blocks = self._blocks([("p1", "![remote](https://example.com/a.png)")])
        server = self._make_server(blocks, "![remote](https://example.com/a.png)")
        with mock.patch.object(mcp_server, "fetch_url_bytes", return_value=self.png_bytes) as fake_fetch:
            response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        image_items = [item for item in response["result"]["content"] if item["type"] == "image"]
        self.assertEqual(len(image_items), 1)
        self.assertEqual(image_items[0]["mimeType"], "image/png")
        fake_fetch.assert_called_once()
        self.assertEqual(fake_fetch.call_args[0][0], "https://example.com/a.png")
        self.assertEqual(fake_fetch.call_args[0][1], (9 * 1024 * 1024 // 4) * 3)
        self.assertIn("[图片已内联：https://example.com/a.png]", self._text(response["result"]["content"]))

    def test_network_failure_declared_in_place(self):
        blocks = self._blocks([("p1", "![remote](https://example.com/a.png)")])
        server = self._make_server(blocks, "![remote](https://example.com/a.png)")
        with mock.patch.object(mcp_server, "fetch_url_bytes", side_effect=mcp_server.URLError("boom")):
            response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        content = response["result"]["content"]
        self.assertTrue(all(item["type"] == "text" for item in content))
        self.assertIn("[图片未返回：读取失败", self._text(content))
        self.assertIn("https://example.com/a.png", self._text(content))

    def test_image_token_cost_counts_toward_window_budget(self):
        items = [(f"p{i}", f"![img{i}](assets/img{i}.png)") for i in range(1, 4)]
        blocks = self._blocks(items)
        doc_md = "\n\n".join(md for _, md in items)
        server = self._make_server(blocks, doc_md)
        result = server.siyuan_read({"document_id": "doc1", "token_budget": 3000})
        self.assertIn("展示块：1-1 / 3", result)
        self.assertIn("继续阅读：`block_start=2", result)
        self.assertIn("1,5", result.split("估算令牌数：", 1)[1][:12])

    def test_build_read_content_interleaves_text_and_images(self):
        images = [
            {"data": "AAAA", "mimeType": "image/png", "source": "C:/a.png"},
            {"data": "BBBB", "mimeType": "image/jpeg", "source": "https://x/b.jpg"},
        ]
        parts = mcp_server.build_read_content(
            "Header\n\nA\n\n@@SIYUAN-IMAGE:0@@\n\nB\n\n@@SIYUAN-IMAGE:1@@\n\nTail",
            images,
        )
        self.assertEqual(
            [part["type"] for part in parts],
            ["text", "text", "image", "text", "text", "image", "text"],
        )
        self.assertIn("[图片已内联：C:/a.png]", parts[1]["text"])
        self.assertEqual(parts[2]["data"], "AAAA")
        self.assertEqual(parts[2]["mimeType"], "image/png")
        self.assertIn("B", parts[3]["text"])
        self.assertIn("[图片已内联：https://x/b.jpg]", parts[4]["text"])
        self.assertEqual(parts[5]["data"], "BBBB")

    def test_image_with_title_argument_is_inlined(self):
        blocks = self._blocks([("p1", '![chart](assets/chart.png "图片标题")')])
        server = self._make_server(blocks, '![chart](assets/chart.png "图片标题")')
        response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        content = response["result"]["content"]
        image_items = [item for item in content if item["type"] == "image"]
        self.assertEqual(len(image_items), 1)
        self.assertEqual(image_items[0]["mimeType"], "image/png")

    def test_network_image_without_extension_is_sniffed_by_magic(self):
        blocks = self._blocks([("p1", "![remote](https://th.example.com/id/R.064eabc?rik=1&r=0)")])
        server = self._make_server(blocks, "![remote](https://th.example.com/id/R.064eabc?rik=1&r=0)")
        with mock.patch.object(mcp_server, "fetch_url_bytes", return_value=self.png_bytes):
            response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        image_items = [item for item in response["result"]["content"] if item["type"] == "image"]
        self.assertEqual(len(image_items), 1)
        self.assertEqual(image_items[0]["mimeType"], "image/png")

    def test_network_non_image_content_is_declared(self):
        blocks = self._blocks([("p1", "![remote](https://example.com/page)")])
        server = self._make_server(blocks, "![remote](https://example.com/page)")
        with mock.patch.object(mcp_server, "fetch_url_bytes", return_value=b"<html>not an image</html>"):
            response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        content = response["result"]["content"]
        self.assertTrue(all(item["type"] == "text" for item in content))
        self.assertIn("无法识别图片格式", self._text(content))

    def test_local_asset_without_extension_is_sniffed_by_magic(self):
        blocks = self._blocks([("p1", "![pic](assets/noext)")])
        server = self._make_server(blocks, "![pic](assets/noext)")
        response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        image_items = [item for item in response["result"]["content"] if item["type"] == "image"]
        self.assertEqual(len(image_items), 1)
        self.assertEqual(image_items[0]["mimeType"], "image/png")

    def test_budget_prefix_filling_stops_and_declares_rest(self):
        items = [
            ("p1", "![a](assets/a.png)"),
            ("p2", "![b](assets/b.png)"),
            ("p3", "![c](assets/c.png)"),
            ("p4", "![d](assets/d.png)"),
        ]
        blocks = self._blocks(items)
        doc_md = "\n\n".join(md for _, md in items)
        server = self._make_server(blocks, doc_md)
        # png_bytes base64 后 76 字节：预算 100 只装得下第 1 张，
        # 第 2 张触发停止，第 2-4 张全部声明（前缀装填）。
        with mock.patch.object(mcp_server, "INLINE_RESPONSE_BUDGET_BYTES", 100):
            response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        content = response["result"]["content"]
        image_items = [item for item in content if item["type"] == "image"]
        self.assertEqual(len(image_items), 1)
        text = self._text(content)
        self.assertIn("[图片已内联：", text)
        self.assertIn("[图片未返回：超出单笔", text)
        self.assertIn("预算已被前面的图片占满", text)
        self.assertIn("b.png", text)
        self.assertIn("d.png", text)
        self.assertIn("超出单笔 100 字节 图片总量安全范围未返回", text)

    def test_first_image_over_budget_declares_all_without_inlining(self):
        items = [("p1", "![a](assets/a.png)"), ("p2", "![b](assets/b.png)")]
        blocks = self._blocks(items)
        doc_md = "\n\n".join(md for _, md in items)
        server = self._make_server(blocks, doc_md)
        with mock.patch.object(mcp_server, "INLINE_RESPONSE_BUDGET_BYTES", 10):
            response = server.call_tool(1, "siyuan_read", {"document_id": "doc1"})
        content = response["result"]["content"]
        self.assertTrue(all(item["type"] == "text" for item in content))
        text = self._text(content)
        self.assertEqual(text.count("[图片未返回：超出单笔"), 1)
        self.assertEqual(text.count("预算已被前面的图片占满"), 1)
        self.assertIn("超出单笔 10 字节 图片总量安全范围未返回", text)

    def test_large_images_flag_inlines_beyond_budget(self):
        items = [("p1", "![a](assets/a.png)"), ("p2", "![b](assets/b.png)")]
        blocks = self._blocks(items)
        doc_md = "\n\n".join(md for _, md in items)
        server = self._make_server(blocks, doc_md)
        with mock.patch.object(mcp_server, "INLINE_RESPONSE_BUDGET_BYTES", 10):
            response = server.call_tool(
                1, "siyuan_read", {"document_id": "doc1", "include_large_images": True}
            )
        content = response["result"]["content"]
        image_items = [item for item in content if item["type"] == "image"]
        self.assertEqual(len(image_items), 2)
        self.assertNotIn("图片总量安全范围", self._text(content))

    def test_tool_spec_documents_large_image_confirm(self):
        spec = next(tool for tool in mcp_server.tool_specs() if tool["name"] == "siyuan_read")
        self.assertIn("include_large_images", spec["inputSchema"]["properties"])
        description = spec["inputSchema"]["properties"]["include_large_images"]["description"]
        self.assertIn("include_images", spec["inputSchema"]["properties"])
        self.assertIn("ignore the per-call image budget", description)
        self.assertIn("10 MB", description)


class LoadConfigInlineImagesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / ".test_tmp" / "config_inline"
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    def test_defaults_to_false_when_key_missing(self):
        (self.root / "config.local.json").write_text(
            json.dumps({"profiles": []}), encoding="utf-8"
        )
        self.assertFalse(load_config(self.root).read_inline_images)

    def test_true_only_when_explicitly_true(self):
        (self.root / "config.local.json").write_text(
            json.dumps({"profiles": [], "read_inline_images": True}), encoding="utf-8"
        )
        self.assertTrue(load_config(self.root).read_inline_images)
        (self.root / "config.local.json").write_text(
            json.dumps({"profiles": [], "read_inline_images": "yes"}), encoding="utf-8"
        )
        self.assertFalse(load_config(self.root).read_inline_images)


if __name__ == "__main__":
    unittest.main()
