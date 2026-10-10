from __future__ import annotations

import unittest

from source_code.agent_notebook import (
    PrivacyRulesUnavailableError,
    is_privacy_rules_document,
    load_agent_notebook,
)


class FakeSystemClient:
    def __init__(self, notebook_name="思源桥"):
        self.notebooks = [{"id": "system-nb", "name": notebook_name, "closed": False}]
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
        self.client = FakeSystemClient()

    def _complete_documents(self):
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

    def test_reads_documents_by_name_without_any_state_file(self):
        self._complete_documents()

        state = load_agent_notebook(self.client, None, "zh-CN")

        self.assertEqual(state.notebook_id, "system-nb")
        self.assertEqual(state.notebook_name, "思源桥")
        self.assertEqual(state.ai_guide_markdown, "要求一")
        self.assertEqual(state.mcp_usage_guide_markdown, "指南一")
        self.assertEqual(state.privacy_rules_doc_ids, ("privacy-1",))
        self.assertEqual(state.missing_document_keys, ())

    def test_merges_multiple_documents_with_same_name(self):
        self._complete_documents()
        self.client.add_doc("pref-2", "用户个性化要求", "要求二")
        self.client.add_doc("privacy-2", "隐私规则", "")

        state = load_agent_notebook(self.client, None)

        self.assertEqual(state.document_ids["ai_guide"], ("pref-1", "pref-2"))
        self.assertEqual(state.ai_guide_markdown, "要求一\n\n---\n\n要求二")
        self.assertEqual(state.privacy_rules_doc_ids, ("privacy-1", "privacy-2"))

    def test_legacy_names_are_recognized(self):
        self._complete_documents()
        del self.client.docs["pref-1"]
        del self.client.docs["privacy-1"]
        self.client.add_doc("legacy-pref", "AI Guide", "旧要求")
        self.client.add_doc("legacy-privacy", "Privacy Rules", "")

        state = load_agent_notebook(self.client, None)

        self.assertEqual(state.document_ids["ai_guide"], ("legacy-pref",))
        self.assertEqual(state.ai_guide_markdown, "旧要求")
        self.assertEqual(state.privacy_rules_doc_ids, ("legacy-privacy",))
        self.assertEqual(state.missing_document_keys, ())

    def test_language_follows_notebook_name(self):
        self._complete_documents()
        self.client.notebooks = [{"id": "system-nb", "name": "SiYuan Bridge", "closed": False}]

        state = load_agent_notebook(self.client, None, None)

        self.assertEqual(state.language, "en")
        self.assertEqual(state.notebook_name, "SiYuan Bridge")

    def test_legacy_notebook_name_is_recognized(self):
        self._complete_documents()
        self.client.notebooks = [{"id": "legacy-nb", "name": "SiYuan Agent Bridge", "closed": False}]

        state = load_agent_notebook(self.client, None)

        self.assertEqual(state.notebook_id, "legacy-nb")

    def test_multiple_same_name_notebooks_take_first(self):
        self._complete_documents()
        self.client.notebooks = [
            {"id": "nb-first", "name": "思源桥", "closed": False},
            {"id": "nb-second", "name": "思源桥", "closed": False},
        ]
        seen_boxes = []
        original_query = self.client.query_sql

        def query_sql(stmt):
            if "WHERE type='d' AND box=" in stmt:
                box = stmt.rsplit("'", 2)[-2]
                seen_boxes.append(box)
            return original_query(stmt)

        self.client.query_sql = query_sql

        state = load_agent_notebook(self.client, None)

        self.assertEqual(state.notebook_id, "nb-first")
        self.assertEqual(seen_boxes, ["nb-first"])

    def test_non_privacy_missing_is_warning_state(self):
        self._complete_documents()
        del self.client.docs["about-1"]

        state = load_agent_notebook(self.client, None)

        self.assertIn("about", state.missing_document_keys)
        self.assertEqual(state.privacy_rules_doc_ids, ("privacy-1",))

    def test_all_privacy_documents_missing_fails_closed(self):
        self._complete_documents()
        del self.client.docs["privacy-1"]

        with self.assertRaisesRegex(PrivacyRulesUnavailableError, "禁用并重新启用"):
            load_agent_notebook(self.client, None)

    def test_missing_system_notebook_fails_closed(self):
        self.client.notebooks = [{"id": "other", "name": "普通笔记本", "closed": False}]

        with self.assertRaisesRegex(PrivacyRulesUnavailableError, "禁用并重新启用"):
            load_agent_notebook(self.client, None)

    def test_renamed_privacy_document_is_no_longer_recognized(self):
        # 行为变化（需求 3.3）：改名的 Privacy Rules 文档脱离系统文档身份。
        self._complete_documents()
        renamed = self.client.docs["privacy-1"]
        renamed["hpath"] = "/我的隐私备份"

        with self.assertRaises(PrivacyRulesUnavailableError):
            load_agent_notebook(self.client, None)


class PrivacyRulesNameMatchTests(unittest.TestCase):
    def test_matches_current_and_legacy_names_case_insensitive(self):
        self.assertTrue(is_privacy_rules_document("/隐私规则"))
        self.assertTrue(is_privacy_rules_document("/Privacy Rules"))
        self.assertTrue(is_privacy_rules_document("/privacy rules"))
        self.assertFalse(is_privacy_rules_document("/Projects/隐私规则"))
        self.assertFalse(is_privacy_rules_document("/隐私规则备份"))
        self.assertFalse(is_privacy_rules_document(""))

    def test_name_match_does_not_depend_on_notebook_or_document_id(self):
        # 纯名称匹配：不再有登记 ID 或系统笔记本限定参数。
        self.assertTrue(is_privacy_rules_document("/Privacy Rules"))


if __name__ == "__main__":
    unittest.main()
