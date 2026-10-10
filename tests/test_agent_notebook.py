from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from source_code.agent_notebook import (
    PrivacyRulesUnavailableError,
    collect_system_notebook_ids,
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
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        guides = self.root / "templates" / "guides"
        guides.mkdir(parents=True)
        (guides / "mcp-usage-guide.zh-CN.md").write_text("内置中文指南", encoding="utf-8")
        (guides / "mcp-usage-guide.en.md").write_text("built-in english guide", encoding="utf-8")
        (guides / "workspace-index-guide.zh-CN.md").write_text("索引指南", encoding="utf-8")
        (guides / "workspace-index-guide.en.md").write_text("index guide", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _complete_documents(self):
        data = {
            "ai_guide": ("pref-1", "用户个性化要求", "要求一"),
            "workspace_index": ("index-1", "工作空间索引", "索引一"),
            "about": ("about-1", "关于思源桥", "关于"),
            "privacy_rules": ("privacy-1", "隐私规则", ""),
        }
        for key, (doc_id, title, markdown) in data.items():
            self.client.add_doc(doc_id, title, markdown)

    def test_reads_four_documents_by_name_without_any_state_file(self):
        self._complete_documents()

        state = load_agent_notebook(self.client, self.root, "zh-CN")

        self.assertEqual(state.notebook_id, "system-nb")
        self.assertEqual(state.notebook_name, "思源桥")
        self.assertEqual(state.ai_guide_markdown, "要求一")
        self.assertEqual(state.privacy_rules_doc_ids, ("privacy-1",))
        self.assertEqual(state.missing_document_keys, ())

    def test_guides_come_from_built_in_assets_not_from_siyuan(self):
        self._complete_documents()

        state = load_agent_notebook(self.client, self.root, "zh-CN")

        self.assertEqual(state.mcp_usage_guide_markdown, "内置中文指南")
        self.assertTrue(
            state.workspace_index_guide_path.endswith("workspace-index-guide.zh-CN.md")
        )
        self.assertEqual(
            state.workspace_index_guide_path,
            str((self.root / "templates" / "guides" / "workspace-index-guide.zh-CN.md").absolute()),
        )
        # 系统笔记本里的同名旧文档不再被读取为指南。
        self.assertNotIn("mcp_usage_guide", state.document_ids)
        self.assertNotIn("workspace_index_guide", state.document_ids)

    def test_english_language_selects_english_guide_assets(self):
        self._complete_documents()
        self.client.notebooks = [{"id": "system-nb", "name": "SiYuan Bridge", "closed": False}]

        state = load_agent_notebook(self.client, self.root, None)

        self.assertEqual(state.language, "en")
        self.assertEqual(state.mcp_usage_guide_markdown, "built-in english guide")
        self.assertTrue(state.workspace_index_guide_path.endswith("workspace-index-guide.en.md"))

    def test_missing_guide_assets_do_not_break_startup(self):
        self._complete_documents()
        (self.root / "templates" / "guides" / "mcp-usage-guide.zh-CN.md").unlink()

        state = load_agent_notebook(self.client, self.root, "zh-CN")

        self.assertEqual(state.mcp_usage_guide_markdown, "")
        self.assertTrue(state.workspace_index_guide_path.endswith("workspace-index-guide.zh-CN.md"))

    def test_merges_multiple_documents_with_same_name(self):
        self._complete_documents()
        self.client.add_doc("pref-2", "用户个性化要求", "要求二")
        self.client.add_doc("privacy-2", "隐私规则", "")

        state = load_agent_notebook(self.client, self.root)

        self.assertEqual(state.document_ids["ai_guide"], ("pref-1", "pref-2"))
        self.assertEqual(state.ai_guide_markdown, "要求一\n\n---\n\n要求二")
        self.assertEqual(state.privacy_rules_doc_ids, ("privacy-1", "privacy-2"))

    def test_legacy_names_are_recognized(self):
        self._complete_documents()
        del self.client.docs["pref-1"]
        del self.client.docs["privacy-1"]
        self.client.add_doc("legacy-pref", "AI Guide", "旧要求")
        self.client.add_doc("legacy-privacy", "Privacy Rules", "")

        state = load_agent_notebook(self.client, self.root)

        self.assertEqual(state.document_ids["ai_guide"], ("legacy-pref",))
        self.assertEqual(state.ai_guide_markdown, "旧要求")
        self.assertEqual(state.privacy_rules_doc_ids, ("legacy-privacy",))
        self.assertEqual(state.missing_document_keys, ())

    def test_legacy_notebook_name_is_recognized(self):
        self._complete_documents()
        self.client.notebooks = [{"id": "legacy-nb", "name": "SiYuan Agent Bridge", "closed": False}]

        state = load_agent_notebook(self.client, self.root)

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

        state = load_agent_notebook(self.client, self.root)

        self.assertEqual(state.notebook_id, "nb-first")
        self.assertEqual(seen_boxes, ["nb-first"])

    def test_non_privacy_missing_is_warning_state(self):
        self._complete_documents()
        del self.client.docs["about-1"]

        state = load_agent_notebook(self.client, self.root)

        self.assertIn("about", state.missing_document_keys)
        self.assertEqual(state.privacy_rules_doc_ids, ("privacy-1",))

    def test_all_privacy_documents_missing_fails_closed(self):
        self._complete_documents()
        del self.client.docs["privacy-1"]

        with self.assertRaisesRegex(PrivacyRulesUnavailableError, "禁用并重新启用"):
            load_agent_notebook(self.client, self.root)

    def test_missing_system_notebook_fails_closed(self):
        self.client.notebooks = [{"id": "other", "name": "普通笔记本", "closed": False}]

        with self.assertRaisesRegex(PrivacyRulesUnavailableError, "禁用并重新启用"):
            load_agent_notebook(self.client, self.root)

    def test_renamed_privacy_document_is_no_longer_recognized(self):
        # 行为变化（需求 3.3）：改名的 Privacy Rules 文档脱离系统文档身份。
        self._complete_documents()
        renamed = self.client.docs["privacy-1"]
        renamed["hpath"] = "/我的隐私备份"

        with self.assertRaises(PrivacyRulesUnavailableError):
            load_agent_notebook(self.client, self.root)

    def test_retired_guide_documents_stay_ordinary_documents(self):
        # 1.11.2 起 MCP 使用指南与索引创建指南不再属于系统文档，同名文档按普通文档处理。
        self._complete_documents()
        self.client.add_doc("old-guide", "MCP 使用指南", "旧版指南")
        self.client.add_doc("old-index-guide", "工作空间索引创建指南", "旧版索引指南")

        state = load_agent_notebook(self.client, self.root)

        self.assertEqual(state.missing_document_keys, ())
        self.assertEqual(state.mcp_usage_guide_markdown, "内置中文指南")


class PrivacyRulesNameMatchTests(unittest.TestCase):
    def test_matches_only_inside_system_notebook(self):
        system_ids = frozenset({"system-nb"})
        for hpath in ("/隐私规则", "/Privacy Rules", "/privacy rules"):
            self.assertTrue(is_privacy_rules_document(
                hpath, notebook_id="system-nb", system_notebook_ids=system_ids
            ))
        self.assertFalse(is_privacy_rules_document(
            "/隐私规则", notebook_id="other-nb", system_notebook_ids=system_ids
        ))
        self.assertFalse(is_privacy_rules_document(
            "/隐私规则", notebook_id="system-nb", system_notebook_ids=frozenset()
        ))
        self.assertFalse(is_privacy_rules_document(
            "/隐私规则", notebook_id="", system_notebook_ids=system_ids
        ))

    def test_same_name_outside_system_notebook_stays_ordinary(self):
        # 用户其他笔记本下的同名普通文档不受硬隔离。
        self.assertFalse(is_privacy_rules_document(
            "/Projects/隐私规则", notebook_id="user-nb", system_notebook_ids=frozenset({"system-nb"})
        ))

    def test_collect_system_notebook_ids_matches_current_and_legacy_names(self):
        class FakeNotebooks:
            def __init__(self, items):
                self.items = items

            def list_notebooks(self):
                return self.items

        client = FakeNotebooks([
            {"id": "nb-1", "name": "思源桥"},
            {"id": "nb-2", "name": "SiYuan Agent Bridge"},
            {"id": "nb-3", "name": "普通笔记本"},
            {"id": "", "name": "思源桥"},
        ])
        self.assertEqual(
            collect_system_notebook_ids(client), frozenset({"nb-1", "nb-2"})
        )


if __name__ == "__main__":
    unittest.main()
