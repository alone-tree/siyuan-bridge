from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib import error

from source_code.client import SiYuanApiError, SiYuanClient, SiYuanConnectionError, SiYuanTimeoutError


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ClientTests(unittest.TestCase):
    def test_token_header_is_sent(self):
        seen = {}

        def transport(req, timeout):
            seen["auth"] = req.get_header("Authorization")
            seen["timeout"] = timeout
            return FakeResponse({"code": 0, "data": {"version": "3.0.0"}})

        client = SiYuanClient("http://127.0.0.1:6806", token="secret", timeout=7, transport=transport)

        self.assertEqual(client.version(), "3.0.0")
        self.assertEqual(seen["auth"], "Token secret")
        self.assertEqual(seen["timeout"], 7)

    def test_connection_errors_are_wrapped(self):
        def transport(req, timeout):
            raise OSError("boom")

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)

        with self.assertRaises(SiYuanConnectionError):
            client.version()

    def test_timeouts_are_distinct_connection_errors(self):
        def transport(req, timeout):
            raise TimeoutError("slow")

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)

        with self.assertRaises(SiYuanTimeoutError):
            client.version()

    def test_query_sql_with_info_readonly_payload_and_metadata(self):
        seen = {}
        envelope = {"code": 0, "msg": "", "data": [{"id": "b1"}, "bad", None],
                    "limit": 64, "truncated": True}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(envelope)

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        result = client.query_sql_with_info("SELECT id FROM blocks")
        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/query/sql")
        self.assertEqual(seen["body"], {"stmt": "SELECT id FROM blocks", "mode": "readonly"})
        self.assertEqual(result, {**envelope, "data": [{"id": "b1"}]})

    def test_query_sql_with_info_preserves_false_truncated_and_empty_data(self):
        envelope = {"code": 0, "data": [], "limit": 1, "truncated": False}
        client = SiYuanClient("http://127.0.0.1:6806",
                              transport=lambda req, timeout: FakeResponse(envelope))
        self.assertEqual(client.query_sql_with_info("SELECT id FROM blocks LIMIT 1"), envelope)

    def test_query_sql_legacy_payload_and_list_return_remain_compatible(self):
        seen = []

        def transport(req, timeout):
            seen.append(json.loads(req.data.decode("utf-8")))
            return FakeResponse({"code": 0, "data": [{"id": "b1"}, "bad"],
                                 "limit": 64, "truncated": True})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        self.assertEqual(client.query_sql("SELECT id FROM blocks"), [{"id": "b1"}])
        self.assertEqual(seen, [{"stmt": "SELECT id FROM blocks"}])

    def test_query_sql_with_info_rejects_invalid_data_and_api_errors(self):
        for data in (None, {}, "bad", 1):
            with self.subTest(data=data):
                client = SiYuanClient("http://127.0.0.1:6806",
                                      transport=lambda req, timeout: FakeResponse({"code": 0, "data": data}))
                with self.assertRaisesRegex(SiYuanApiError, "Unexpected SQL response shape"):
                    client.query_sql_with_info("SELECT id FROM blocks")
        client = SiYuanClient("http://127.0.0.1:6806", transport=lambda req, timeout:
                              FakeResponse({"code": -1, "msg": "SQL denied", "data": []}))
        with self.assertRaisesRegex(SiYuanApiError, "SQL denied") as ctx:
            client.query_sql_with_info("SELECT id FROM blocks")
        self.assertEqual(ctx.exception.code, -1)

    def test_create_snapshot_posts_memo_only(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {"id": "20260503080000-abc123", "created": "20260503080000"}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)

        snapshot = client.create_snapshot("before edit")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/repo/createSnapshot")
        self.assertEqual(seen["body"], {"memo": "before edit"})
        self.assertEqual(snapshot["id"], "20260503080000-abc123")

    def test_create_snapshot_accepts_null_data(self):
        def transport(req, timeout):
            return FakeResponse({"code": 0, "data": None})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)

        self.assertEqual(client.create_snapshot("before edit"), {})

    def test_get_repo_snapshots_posts_page(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {"snapshots": [], "pageCount": 1, "totalCount": 0}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)

        data = client.get_repo_snapshots(page=2)

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/repo/getRepoSnapshots")
        self.assertEqual(seen["body"], {"page": 2})
        self.assertEqual(data["totalCount"], 0)

    def test_perform_sync_uses_default_siyuan_sync(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            seen["timeout"] = timeout
            return FakeResponse({"code": 0, "data": None})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)

        self.assertEqual(client.perform_sync(), {})
        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/sync/performSync")
        self.assertEqual(seen["body"], {})
        self.assertEqual(seen["timeout"], 10.0)

    def test_get_sync_info_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {"stat": "Synced", "synced": 123}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)

        info = client.get_sync_info()
        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/sync/getSyncInfo")
        self.assertEqual(seen["body"], {})
        self.assertEqual(info["stat"], "Synced")


    def test_create_doc_with_md_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {"id": "20260503090000-newdoc"}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)

        result = client.create_doc_with_md("nb1", "/Test Doc", "# Hello\n\nWorld")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/filetree/createDocWithMd")
        self.assertEqual(seen["body"], {"notebook": "nb1", "path": "/Test Doc", "markdown": "# Hello\n\nWorld"})
        self.assertEqual(result["id"], "20260503090000-newdoc")

    def test_create_doc_with_md_accepts_null_data(self):
        def transport(req, timeout):
            return FakeResponse({"code": 0, "data": None})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        self.assertEqual(client.create_doc_with_md("nb1", "/Test", "# Hi"), {})

    def test_create_doc_with_md_accepts_string_id(self):
        def transport(req, timeout):
            return FakeResponse({"code": 0, "data": "20260503090000-newdoc-string"})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        result = client.create_doc_with_md("nb1", "/Test", "# Hi")
        self.assertEqual(result["id"], "20260503090000-newdoc-string")

    def test_get_hpath_by_id_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": "/Projects/Test"})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        result = client.get_hpath_by_id("doc1")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/filetree/getHPathByID")
        self.assertEqual(seen["body"], {"id": "doc1"})
        self.assertEqual(result, "/Projects/Test")

    def test_rename_doc_by_id_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        client.rename_doc_by_id("doc1", "New Title")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/filetree/renameDocByID")
        self.assertEqual(seen["body"], {"id": "doc1", "title": "New Title"})

    def test_remove_doc_by_id_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        client.remove_doc_by_id("doc1")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/filetree/removeDocByID")
        self.assertEqual(seen["body"], {"id": "doc1"})

    def test_move_docs_by_id_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        client.move_docs_by_id(["doc1"], "target")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/filetree/moveDocsByID")
        self.assertEqual(seen["body"], {"fromIDs": ["doc1"], "toID": "target"})

    def test_duplicate_doc_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {"id": "copy1"}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        result = client.duplicate_doc("doc1")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/filetree/duplicateDoc")
        self.assertEqual(seen["body"], {"id": "doc1"})
        self.assertEqual(result["id"], "copy1")

    def test_update_block_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        client.update_block("block123", "new content")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/block/updateBlock")
        self.assertEqual(seen["body"], {"id": "block123", "dataType": "markdown", "data": "new content"})

    def test_append_block_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        client.append_block("doc1", "appended text")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/block/appendBlock")
        self.assertEqual(seen["body"], {"dataType": "markdown", "data": "appended text", "parentID": "doc1"})

    def test_insert_block_after_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": {}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        client.insert_block_after("prev123", "inserted text")

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/block/insertBlock")
        self.assertEqual(seen["body"], {"dataType": "markdown", "data": "inserted text", "previousID": "prev123"})

    def test_insert_local_assets_payload_and_succ_map(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({
                "code": 0,
                "data": {"succMap": {"chart.png": "assets/chart-abc.png"}},
            })

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        result = client.insert_local_assets("doc1", [r"D:\files\chart.png"])

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/asset/insertLocalAssets")
        self.assertEqual(seen["body"], {
            "id": "doc1",
            "assetPaths": [r"D:\files\chart.png"],
            "isUpload": True,
        })
        self.assertEqual(result, {"chart.png": "assets/chart-abc.png"})

    def test_insert_local_assets_rejects_missing_succ_map(self):
        def transport(req, timeout):
            return FakeResponse({"code": 0, "data": {}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        with self.assertRaises(SiYuanApiError):
            client.insert_local_assets("doc1", [r"D:\files\chart.png"])

    def test_push_msg_payload(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": None})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        client.push_msg("Hello SiYuan", timeout=5000)

        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/notification/pushMsg")
        self.assertEqual(seen["body"], {"msg": "Hello SiYuan", "timeout": 5000})

    def test_list_document_blocks_returns_block_list(self):
        fake_blocks = [
            {"id": "block1", "parent_id": "doc1", "root_id": "doc1", "type": "p", "subtype": "", "markdown": "First para.", "content": "", "sort": 1},
            {"id": "block2", "parent_id": "doc1", "root_id": "doc1", "type": "h", "subtype": "h2", "markdown": "## Heading", "content": "", "sort": 2},
        ]

        def transport(req, timeout):
            return FakeResponse({"code": 0, "data": fake_blocks})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        blocks = client.list_document_blocks("doc1")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["id"], "block1")
        self.assertEqual(blocks[1]["type"], "h")

    def test_list_document_blocks_returns_empty_list(self):
        def transport(req, timeout):
            return FakeResponse({"code": 0, "data": []})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        blocks = client.list_document_blocks("doc1")
        self.assertEqual(blocks, [])

    def test_list_document_blocks_non_list_raises(self):
        def transport(req, timeout):
            return FakeResponse({"code": 0, "data": {"foo": "bar"}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        with self.assertRaises(SiYuanApiError):
            client.list_document_blocks("doc1")

    def test_list_block_references_queries_refs_and_source_blocks(self):
        seen = []
        fake_refs = [{
            "def_block_id": "target1",
            "block_id": "source1",
            "root_id": "doc2",
            "type": "textmark",
            "content": "Visible reference",
            "markdown": "((target1))",
            "block_type": "p",
        }]

        def transport(req, timeout):
            body = json.loads(req.data.decode("utf-8"))
            seen.append(body)
            data = fake_refs if "FROM refs" in body["stmt"] else []
            return FakeResponse({"code": 0, "data": data})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        rows = client.list_block_references(["target1", "target2", "target1"])

        self.assertEqual(len(seen), 2)
        ref_stmt = seen[0]["stmt"]
        link_stmt = seen[1]["stmt"]
        self.assertIn("FROM refs r LEFT JOIN blocks b", ref_stmt)
        self.assertIn("'target1'", ref_stmt)
        self.assertIn("'target2'", ref_stmt)
        self.assertIn("FROM spans s LEFT JOIN blocks b", link_stmt)
        self.assertIn("siyuan://blocks/target1", link_stmt)
        self.assertEqual(rows, fake_refs)

    def test_list_block_references_adds_links_and_deduplicates_same_source_target(self):
        def transport(req, timeout):
            stmt = json.loads(req.data.decode("utf-8"))["stmt"]
            if "FROM refs" in stmt:
                data = [{
                    "def_block_id": "target1",
                    "block_id": "source1",
                    "root_id": "doc2",
                    "type": "textmark",
                    "content": "Reference",
                    "markdown": "((target1)) and [link](siyuan://blocks/target1)",
                    "block_type": "p",
                }]
            else:
                data = [
                    {
                        "block_id": "source1",
                        "root_id": "doc2",
                        "span_markdown": "[link](siyuan://blocks/target1)",
                        "content": "Reference",
                        "markdown": "((target1)) and [link](siyuan://blocks/target1)",
                        "block_type": "p",
                    },
                    {
                        "block_id": "source2",
                        "root_id": "doc2",
                        "span_markdown": "[other](siyuan://blocks/target2)",
                        "content": "Other link",
                        "markdown": "[other](siyuan://blocks/target2)",
                        "block_type": "p",
                    },
                ]
            return FakeResponse({"code": 0, "data": data})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        rows = client.list_block_references(["target1", "target2"])

        self.assertEqual(
            [(row["def_block_id"], row["block_id"]) for row in rows],
            [("target1", "source1"), ("target2", "source2")],
        )
        self.assertEqual(rows[1]["type"], "block-link")

    def test_list_forward_block_references_queries_sources_and_resolves_targets(self):
        seen = []

        def transport(req, timeout):
            stmt = json.loads(req.data.decode("utf-8"))["stmt"]
            seen.append(stmt)
            if "FROM refs" in stmt:
                data = [{
                    "def_block_id": "target1",
                    "block_id": "source1",
                    "root_id": "doc1",
                    "type": "textmark",
                }]
            elif "FROM spans" in stmt:
                data = [{
                    "block_id": "source1",
                    "root_id": "doc1",
                    "span_markdown": "[link](siyuan://blocks/target1) [other](siyuan://blocks/missing)",
                }]
            else:
                data = [{
                    "id": "target1",
                    "root_id": "doc2",
                    "content": "Target paragraph",
                    "markdown": "Target paragraph",
                    "type": "p",
                }]
            return FakeResponse({"code": 0, "data": data})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        rows = client.list_forward_block_references(["source1", "source1"])

        self.assertEqual(len(seen), 3)
        self.assertIn("FROM refs r WHERE r.block_id IN", seen[0])
        self.assertIn("'source1'", seen[0])
        self.assertNotIn("def_block_id IN", seen[0])
        self.assertIn("FROM spans s WHERE s.block_id IN", seen[1])
        self.assertIn("siyuan://blocks/", seen[1].casefold())
        self.assertIn("WHERE id IN", seen[2])
        self.assertIn("'target1'", seen[2])
        self.assertIn("'missing'", seen[2])
        resolved = {row["def_block_id"]: row for row in rows}
        self.assertEqual(set(resolved), {"target1", "missing"})
        self.assertEqual(resolved["target1"]["target_root_id"], "doc2")
        self.assertEqual(resolved["target1"]["target_markdown"], "Target paragraph")
        self.assertEqual(resolved["missing"]["target_root_id"], "")
        self.assertEqual(resolved["target1"]["type"], "textmark")

    def test_get_child_blocks_posts_parent_id(self):
        seen = {}

        def transport(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse({"code": 0, "data": [{"id": "child1", "type": "p"}]})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        blocks = client.get_child_blocks("parent1")
        self.assertEqual(seen["url"], "http://127.0.0.1:6806/api/block/getChildBlocks")
        self.assertEqual(seen["body"], {"id": "parent1"})
        self.assertEqual(blocks, [{"id": "child1", "type": "p"}])

    def test_get_child_blocks_filters_non_dict_items(self):
        def transport(req, timeout):
            return FakeResponse({"code": 0, "data": [{"id": "child1"}, "bad"]})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        self.assertEqual(client.get_child_blocks("parent1"), [{"id": "child1"}])

    def test_get_child_blocks_non_list_raises(self):
        def transport(req, timeout):
            return FakeResponse({"code": 0, "data": {"foo": "bar"}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        with self.assertRaises(SiYuanApiError):
            client.get_child_blocks("parent1")

    def test_create_notebook_unwraps_current_kernel_response(self):
        def transport(req, timeout):
            return FakeResponse({
                "code": 0,
                "data": {
                    "notebook": {
                        "id": "nb-1",
                        "name": "思源桥",
                    },
                },
            })

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        self.assertEqual(
            client.create_notebook("思源桥"),
            {"id": "nb-1", "name": "思源桥"},
        )


class NativeTransportTests(unittest.TestCase):
    def test_get_conf_unwraps_native_conf_field(self):
        seen = []
        conf = {"readonly": False, "sync": {"enabled": True, "mode": 3}, "repo": {"key": "masked"}}

        def transport(req, timeout):
            seen.append((req.full_url, json.loads(req.data)))
            return FakeResponse({"code": 0, "data": {"conf": conf, "start": False}})

        client = SiYuanClient("http://127.0.0.1:6806", transport=transport)
        self.assertEqual(client.get_conf(), conf)
        self.assertEqual(seen, [("http://127.0.0.1:6806/api/system/getConf", {})])

    def test_get_conf_rejects_wrong_native_shape(self):
        for data in (None, [], {}, {"conf": None}, {"conf": []}, {"sync": {}}):
            with self.subTest(data=data):
                client = SiYuanClient("http://unused", transport=lambda req, timeout:
                                      FakeResponse({"code": 0, "data": data}))
                with self.assertRaises(SiYuanApiError):
                    client.get_conf()

    def test_sync_source_state_returns_unmodified_native_state(self):
        seen = []
        state = {"source": "source-hash", "localID": "local", "cloudID": "", "pending": True}

        def transport(req, timeout):
            seen.append((req.full_url, json.loads(req.data)))
            return FakeResponse({"code": 0, "data": state})

        client = SiYuanClient("http://unused", transport=transport)
        self.assertEqual(client.get_sync_source_state(), state)
        self.assertEqual(seen, [("http://unused/api/sync/getSyncSourceState", {})])

    def test_sync_source_preview_connection_failure_and_timeout_are_never_retried(self):
        for failure, expected_error in ((OSError("dropped"), SiYuanConnectionError),
                                        (TimeoutError("slow"), SiYuanTimeoutError)):
            calls = []

            def transport(req, timeout):
                calls.append(req.full_url)
                raise failure

            with self.subTest(failure=failure):
                client = SiYuanClient("http://unused", transport=transport)
                with self.assertRaises(expected_error):
                    client.get_sync_source_state()
                self.assertEqual(calls, ["http://unused/api/sync/getSyncSourceState"])

    def test_sync_source_missing_endpoint_preserves_http_status_without_retry(self):
        for status in (404, 405, 401, 500):
            calls = []

            def transport(req, timeout):
                calls.append(req.full_url)
                raise error.HTTPError(req.full_url, status, "missing", {}, io.BytesIO(b"native error"))

            with self.subTest(status=status):
                client = SiYuanClient("http://unused", transport=transport)
                with self.assertRaises(SiYuanApiError) as ctx:
                    client.get_sync_source_state()
                self.assertEqual(ctx.exception.status, status)
                self.assertEqual(str(ctx.exception), "native error")
                self.assertEqual(len(calls), 1)

    def test_sync_source_invalid_data_and_business_errors_are_not_swallowed(self):
        for envelope in ({"code": 0, "data": None}, {"code": 0, "data": []},
                         {"code": -1, "msg": "source failed", "data": {}}):
            with self.subTest(envelope=envelope):
                client = SiYuanClient("http://unused", transport=lambda req, timeout: FakeResponse(envelope))
                with self.assertRaises(SiYuanApiError):
                    client.get_sync_source_state()

    def test_perform_sync_only_sends_explicit_direction_and_source_identifiers(self):
        cases = [({}, {}), ({"upload": False}, {"upload": False}),
                 ({"upload": True}, {"upload": True})]
        source = {"source": "hash", "localID": "local", "cloudID": "", "pending": True, "mode": 3}
        binding = {key: source[key] for key in ("source", "localID", "cloudID")}
        cases.extend([({"source_state": source}, binding),
                      ({"upload": False, "source_state": source}, {"upload": False, **binding}),
                      ({"upload": True, "source_state": source}, {"upload": True, **binding})])
        for kwargs, expected in cases:
            seen = []

            def transport(req, timeout):
                seen.append((json.loads(req.data), timeout))
                return FakeResponse({"code": 0, "data": {"native": "result"}})

            with self.subTest(kwargs=kwargs):
                client = SiYuanClient("http://unused", transport=transport)
                self.assertEqual(client.perform_sync(timeout=23, **kwargs), {"native": "result"})
                self.assertEqual(seen, [(expected, 23)])
        self.assertEqual(source["pending"], True)

    def test_perform_sync_connection_failure_and_timeout_are_never_retried(self):
        for failure in (OSError("dropped"), TimeoutError("slow"), error.URLError("network")):
            calls = []

            def transport(req, timeout):
                calls.append(req.full_url)
                raise failure

            with self.subTest(failure=failure):
                client = SiYuanClient("http://unused", transport=transport)
                with self.assertRaises(SiYuanConnectionError):
                    client.perform_sync(upload=False)
                self.assertEqual(len(calls), 1)

    def test_insert_local_assets_result_preserves_partial_error_and_directory_payload(self):
        seen = []
        envelope = {"code": -1, "msg": "one failed", "data": {
            "succMap": {"目录": "file://native-path"},
            "succFiles": [{"index": 0, "name": "目录", "path": "file://native-path"}],
            "failedFiles": [{"index": 1, "name": "missing", "error": "missing"}]}}

        def transport(req, timeout):
            seen.append((req.full_url, json.loads(req.data)))
            return FakeResponse(envelope)

        client = SiYuanClient("http://unused", transport=transport)
        self.assertEqual(client.insert_local_assets_result("doc", ["目录", "missing"]), envelope)
        self.assertEqual(seen, [("http://unused/api/asset/insertLocalAssets", {
            "id": "doc", "assetPaths": ["目录", "missing"], "isUpload": False})])
        client.insert_local_assets_result("doc", ["file"], is_upload=True)
        self.assertTrue(seen[-1][1]["isUpload"])
        with self.assertRaises(SiYuanApiError):
            client.insert_local_assets("doc", ["file"])

    def test_new_local_asset_result_is_not_retried_but_legacy_transport_still_retries(self):
        calls = []

        def transport(req, timeout):
            calls.append(req.full_url)
            raise OSError("connection dropped")

        client = SiYuanClient("http://unused", transport=transport)
        with self.assertRaises(SiYuanConnectionError):
            client.insert_local_assets_result("doc", ["file"], is_upload=True)
        self.assertEqual(len(calls), 1)
        calls.clear()
        with patch("time.sleep"), self.assertRaises(SiYuanConnectionError):
            client.version()
        self.assertEqual(len(calls), 2)

    def test_upload_preparation_failure_closes_already_opened_files_without_request(self):
        streams = []
        original_open = Path.open
        calls = []

        def tracked_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            streams.append(stream)
            return stream

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one.bin"
            path.write_bytes(b"one")
            with patch.object(Path, "open", tracked_open):
                client = SiYuanClient("http://unused", transport=lambda req, timeout: calls.append(req))
                with self.assertRaises(FileNotFoundError):
                    client.upload_assets("doc", [str(path), str(path.parent / "missing.bin")])
            self.assertTrue(all(stream.closed for stream in streams))
            self.assertEqual(calls, [])

    def test_include_envelope_does_not_allow_business_errors_on_other_apis(self):
        client = SiYuanClient("http://unused", transport=lambda req, timeout:
                              FakeResponse({"code": -1, "msg": "denied", "data": {}}))
        for path, allow in (("/api/query/sql", False), ("/api/query/sql", True),
                            ("/api/asset/insertLocalAssets", False)):
            with self.subTest(path=path, allow=allow):
                with self.assertRaisesRegex(SiYuanApiError, "denied"):
                    client._post(path, {}, include_envelope=True, allow_asset_errors=allow)

    def test_upload_assets_real_http_bytes_unicode_same_names_and_content_length(self):
        seen = {}
        envelope = {"code": 0, "msg": "native", "data": {"succMap": {"同名.bin": "assets/b"},
                    "succFiles": [{"index": 0, "path": "assets/a"},
                                  {"index": 1, "path": "assets/b"},
                                  {"index": 2, "path": "assets/empty"}], "errFiles": []}}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["path"] = self.path
                seen["headers"] = self.headers
                seen["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                payload = json.dumps(envelope).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory, HTTPServer(("127.0.0.1", 0), Handler) as server:
            root = Path(directory)
            (root / "one").mkdir()
            (root / "two").mkdir()
            files = [root / "one" / "同名.bin", root / "two" / "同名.bin", root / "空 文件.txt"]
            payloads = [b"\x00\xffnative\r\n", "中文内容".encode("utf-8"), b""]
            for path, data in zip(files, payloads):
                path.write_bytes(data)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = SiYuanClient(f"http://127.0.0.1:{server.server_port}", token="fake-test-token")
                self.assertEqual(client.upload_assets("doc-target", [str(path) for path in files]), envelope)
            finally:
                server.shutdown()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(seen["path"], "/api/asset/upload")
            headers = seen["headers"]
            self.assertEqual(headers["Authorization"], "Token fake-test-token")
            self.assertEqual(headers["Connection"], "close")
            self.assertIsNone(headers["Transfer-Encoding"])
            self.assertEqual(int(headers["Content-Length"]), len(seen["body"]))
            message = BytesParser(policy=policy.default).parsebytes(
                ("Content-Type: " + headers["Content-Type"] + "\r\n\r\n").encode() + seen["body"])
            parts = list(message.iter_parts())
            self.assertEqual(parts[0].get_param("name", header="Content-Disposition"), "id")
            self.assertEqual(parts[0].get_payload(decode=True), b"doc-target")
            self.assertEqual([part.get_param("name", header="Content-Disposition") for part in parts[1:]],
                             ["file[]"] * 3)
            self.assertEqual([part.get_filename() for part in parts[1:]], [path.name for path in files])
            self.assertEqual([part.get_payload(decode=True) for part in parts[1:]], payloads)
            self.assertNotIn(str(root).encode(), seen["body"])

    def test_upload_stream_is_bounded_and_keeps_native_error_envelopes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.bin"
            data = b"a" * (140 * 1024)
            path.write_bytes(data)
            envelopes = [
                {"code": 0, "data": {"succMap": {}, "errFiles": ["small.bin"]}},
                {"code": -1, "msg": "partial", "data": {"succFiles": [{"index": 0, "path": "assets/a"}],
                                                          "failedFiles": [{"index": 1, "error": "denied"}]}}]
            for envelope in envelopes:
                seen = []

                def transport(req, timeout):
                    self.assertFalse(isinstance(req.data, bytes))
                    chunks = list(req.data)
                    self.assertLessEqual(max(map(len, chunks)), 64 * 1024)
                    body = b"".join(chunks)
                    self.assertIn(data, body)
                    self.assertEqual(int(req.get_header("Content-length")), len(body))
                    seen.append(timeout)
                    return FakeResponse(envelope)

                with self.subTest(envelope=envelope), patch.object(Path, "read_bytes", side_effect=AssertionError):
                    client = SiYuanClient("http://unused", timeout=17, transport=transport)
                    self.assertEqual(client.upload_assets("doc", [str(path)]), envelope)
                    self.assertEqual(seen, [17])

    def test_upload_failure_never_retries_and_closes_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.bin"
            path.write_bytes(b"small")
            for failure in (OSError("dropped"), TimeoutError("slow"), error.URLError("network")):
                calls = []
                streams = []
                original_open = Path.open

                def tracked_open(path, *args, **kwargs):
                    stream = original_open(path, *args, **kwargs)
                    streams.append(stream)
                    return stream

                def transport(req, timeout):
                    calls.append(req.full_url)
                    next(iter(req.data))
                    raise failure

                with self.subTest(failure=failure), patch.object(Path, "open", tracked_open):
                    client = SiYuanClient("http://unused", transport=transport)
                    with self.assertRaises(SiYuanConnectionError):
                        client.upload_assets("doc", [str(path)])
                    self.assertEqual(len(calls), 1)
                    self.assertTrue(all(stream.closed for stream in streams))

    def test_upload_http_errors_still_raise_status_and_invalid_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.bin"
            path.write_bytes(b"small")

            def transport(req, timeout):
                raise error.HTTPError(req.full_url, 403, "denied", {}, io.BytesIO(b"forbidden"))

            client = SiYuanClient("http://unused", transport=transport)
            with self.assertRaises(SiYuanApiError) as ctx:
                client.upload_assets("doc", [str(path)])
            self.assertEqual(ctx.exception.status, 403)
            for envelope in ([], None, "bad"):
                client = SiYuanClient("http://unused", transport=lambda req, timeout: FakeResponse(envelope))
                with self.assertRaises(SiYuanApiError):
                    client.upload_assets("doc", [str(path)])


if __name__ == "__main__":
    unittest.main()
