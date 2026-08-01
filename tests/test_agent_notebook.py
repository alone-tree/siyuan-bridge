from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from source_code.agent_notebook import (
    PrivacyRulesUnavailableError,
    is_privacy_rules_document,
    load_agent_notebook,
)
from source_code.system_state import SYSTEM_STATE_SCHEMA_VERSION, load_system_state


class FakeSystemClient:
    def __init__(self):
        self.notebooks = [{"id": "system-nb", "name": "思源桥", "closed": False}]
        self.docs: dict[str, dict] = {}
        self.exports: list[str] = []

    def add_doc(self, doc_id: str, title: str, markdown: str, *, updated="20260701000000"):
        self.docs[doc_id] = {
            "id": doc_id,
            "box": "system-nb",
            "hpath": f"/{title}",
            "path": f"/{doc_id}.sy",
            "markdown": markdown,
            "updated": updated,
        }

    def list_notebooks(self):
        return [dict(item) for item in self.notebooks]

    def query_sql(self, stmt):
        if "WHERE type='d' AND box=" in stmt:
            return [
                {key: value for key, value in doc.items() if key != "markdown"}
                for doc in self.docs.values()
            ]
        return []

    def export_markdown(self, doc_id):
        self.exports.append(doc_id)
        return self.docs[doc_id]["markdown"]

    def open_notebook(self, _notebook_id):
        return None

    def close_notebook(self, _notebook_id):
        return None


class AgentNotebookReadTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.client = FakeSystemClient()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_state(self, documents, *, schema_version=2):
        path = self.root / "knowledge_base/system_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": schema_version,
            "active_workspace_key": "system-nb",
            "workspaces": {
                "system-nb": {
                    "system_notebook": {"id": "system-nb", "name": "思源桥"},
                    "documents": documents,
                }
            },
        }, ensure_ascii=False), encoding="utf-8")

    def _complete_documents(self):
        records = {}
        data = {
            "ai_guide": ("pref-1", "用户个性化要求", "要求一"),
            "mcp_usage_guide": ("mcp-1", "MCP 使用指南", "指南一"),
            "workspace_index_guide": ("wig-1", "工作空间索引创建指南", "创建说明"),
            "workspace_index": ("index-1", "工作空间索引", "索引一"),
            "about": ("about-1", "关于思源桥", "关于"),
            "privacy_rules": ("privacy-1", "隐私规则", ""),
        }
        for key, (doc_id, title, markdown) in data.items():
            self.client.add_doc(doc_id, title, markdown)
            extra = {"updated": "20260701000000", "placeholder": False} if key == "workspace_index" else {}
            records[key] = [{"id": doc_id, "name": title, **extra}]
        return records

    def test_reads_schema_v1_without_writing_or_repairing(self):
        records = self._complete_documents()
        v1_records = {key: entries[0] for key, entries in records.items()}
        self._write_state(v1_records, schema_version=1)
        before = (self.root / "knowledge_base/system_state.json").read_bytes()

        state = load_agent_notebook(self.client, self.root, "zh-CN")

        self.assertEqual(state.ai_guide_markdown, "要求一")
        self.assertEqual(state.privacy_rules_doc_ids, ("privacy-1",))
        self.assertEqual(before, (self.root / "knowledge_base/system_state.json").read_bytes())
        self.assertEqual(load_system_state(self.root)["schema_version"], SYSTEM_STATE_SCHEMA_VERSION)

    def test_merges_multiple_documents_and_skips_stale_ids(self):
        records = self._complete_documents()
        self.client.add_doc("pref-2", "用户个性化要求", "要求二")
        self.client.add_doc("privacy-2", "隐私规则", "")
        records["ai_guide"] = [
            {"id": "missing-pref"}, {"id": "pref-1"}, {"id": "pref-2"}
        ]
        records["privacy_rules"] = [{"id": "privacy-1"}, {"id": "privacy-2"}]
        self._write_state(records)

        state = load_agent_notebook(self.client, self.root)

        self.assertEqual(state.document_ids["ai_guide"], ("pref-1", "pref-2"))
        self.assertEqual(state.ai_guide_markdown, "要求一\n\n---\n\n要求二")
        self.assertEqual(state.privacy_rules_doc_ids, ("privacy-1", "privacy-2"))
        self.assertEqual(state.missing_document_keys, ())

    def test_non_privacy_missing_is_warning_state(self):
        records = self._complete_documents()
        del self.client.docs["about-1"]
        self._write_state(records)

        state = load_agent_notebook(self.client, self.root)

        self.assertIn("about", state.missing_document_keys)
        self.assertEqual(state.privacy_rules_doc_ids, ("privacy-1",))

    def test_all_privacy_documents_missing_fails_closed(self):
        records = self._complete_documents()
        del self.client.docs["privacy-1"]
        self._write_state(records)

        with self.assertRaisesRegex(PrivacyRulesUnavailableError, "禁用并重新启用"):
            load_agent_notebook(self.client, self.root)

    def test_all_registered_privacy_ids_are_hard_hidden(self):
        records = self._complete_documents()
        self.client.add_doc("privacy-2", "隐私规则", "")
        records["privacy_rules"].append({"id": "privacy-2"})
        self._write_state(records)

        self.assertTrue(is_privacy_rules_document(
            "/别的标题", root=self.root, document_id="privacy-2", notebook_id="system-nb"
        ))
        self.assertTrue(is_privacy_rules_document(
            "/隐私规则", root=self.root, document_id="unregistered", notebook_id="system-nb"
        ))
        self.assertFalse(is_privacy_rules_document(
            "/隐私规则", root=self.root, document_id="other", notebook_id="other-nb"
        ))


if __name__ == "__main__":
    unittest.main()
