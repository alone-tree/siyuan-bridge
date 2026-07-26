from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from source_code.agent_notebook import ensure_agent_notebook
from source_code.i18n import (
    LEGACY_AI_GUIDE_TEMPLATES,
    WORKSPACE_INDEX_PLACEHOLDERS,
)
from source_code.system_state import load_system_state


class FakeSystemClient:
    def __init__(self, *, workspace_dir: str = r"D:\SiYuan\workspace"):
        self.workspace_dir = workspace_dir
        self.notebooks = [{"id": "system-nb", "name": "思源桥", "closed": False}]
        self.docs: dict[str, dict] = {}
        self.next_id = 1
        self.renames: list[tuple[str, str]] = []
        self.updates: list[tuple[str, str]] = []

    def add_doc(
        self,
        title: str,
        markdown: str,
        *,
        doc_id: str | None = None,
        notebook_id: str = "system-nb",
        updated: str = "20260701000000",
    ) -> str:
        doc_id = doc_id or f"doc-{self.next_id}"
        self.next_id += 1
        self.docs[doc_id] = {
            "id": doc_id,
            "box": notebook_id,
            "hpath": f"/{title}",
            "path": f"/{doc_id}.sy",
            "markdown": markdown,
            "updated": updated,
        }
        return doc_id

    def get_workspace_dir(self):
        return self.workspace_dir

    def list_notebooks(self):
        return [dict(notebook) for notebook in self.notebooks]

    def create_notebook(self, name):
        notebook = {"id": "created-system-nb", "name": name, "closed": False}
        self.notebooks.append(notebook)
        return dict(notebook)

    def query_sql(self, stmt):
        if "WHERE type='d' AND box=" in stmt:
            notebook_id = stmt.split("box='", 1)[1].split("'", 1)[0]
            return [
                {key: value for key, value in doc.items() if key != "markdown"}
                for doc in self.docs.values()
                if doc["box"] == notebook_id
            ]
        if "SELECT updated FROM blocks WHERE id=" in stmt:
            doc_id = stmt.split("id='", 1)[1].split("'", 1)[0]
            doc = self.docs.get(doc_id)
            return [{"updated": doc["updated"]}] if doc else []
        return []

    def create_doc_with_md(self, notebook, path, markdown):
        return {"id": self.add_doc(path.strip("/"), markdown, notebook_id=notebook)}

    def export_markdown(self, doc_id):
        return self.docs[doc_id]["markdown"]

    def update_block(self, doc_id, markdown):
        self.docs[doc_id]["markdown"] = markdown
        self.docs[doc_id]["updated"] = "20260702000000"
        self.updates.append((doc_id, markdown))
        return {}

    def rename_doc_by_id(self, doc_id, title):
        self.docs[doc_id]["hpath"] = f"/{title}"
        self.renames.append((doc_id, title))
        return {}

    def open_notebook(self, _notebook_id):
        return None

    def close_notebook(self, _notebook_id):
        return None


class AgentNotebookMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        shutil.copytree(Path.cwd() / "templates", self.root / "templates")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _workspace_state(self):
        state = load_system_state(self.root)
        return state["workspaces"]["system-nb"]

    def test_new_install_creates_six_documents_and_registry(self):
        client = FakeSystemClient()

        state = ensure_agent_notebook(client, self.root, "zh-CN")

        titles = {doc["hpath"].strip("/") for doc in client.docs.values()}
        self.assertEqual(
            titles,
            {
                "关于思源桥",
                "隐私规则",
                "MCP 使用指南",
                "工作空间索引创建指南",
                "用户个性化要求",
                "工作空间索引",
            },
        )
        self.assertTrue(state.workspace_index_is_placeholder)
        self.assertIn("用户尚未创建工作空间索引", state.workspace_index_markdown)
        registry = self._workspace_state()
        self.assertEqual(registry["system_notebook"]["id"], "system-nb")
        self.assertEqual(set(registry["documents"]), {
            "about",
            "privacy_rules",
            "mcp_usage_guide",
            "workspace_index_guide",
            "ai_guide",
            "workspace_index",
        })

    def test_old_ai_guide_is_renamed_by_id_and_user_body_is_preserved(self):
        client = FakeSystemClient()
        old_id = client.add_doc("AI 使用指南", "用户自己写给 AI 的要求")

        state = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertEqual(state.ai_guide_doc_id, old_id)
        self.assertEqual(client.docs[old_id]["hpath"], "/用户个性化要求")
        self.assertEqual(client.docs[old_id]["markdown"], "用户自己写给 AI 的要求")
        self.assertEqual(client.renames, [(old_id, "用户个性化要求")])

    def test_known_old_default_is_replaced_with_minimal_template(self):
        client = FakeSystemClient()
        old_id = client.add_doc(
            "AI 使用指南", LEGACY_AI_GUIDE_TEMPLATES["zh-CN"]
        )

        state = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertEqual(state.ai_guide_doc_id, old_id)
        self.assertEqual(client.docs[old_id]["hpath"], "/用户个性化要求")
        self.assertIn("希望 AI 长期遵循", client.docs[old_id]["markdown"])

    def test_current_name_wins_when_old_and_new_documents_both_exist(self):
        client = FakeSystemClient()
        old_id = client.add_doc("AI 使用指南", "旧文档")
        new_id = client.add_doc("用户个性化要求", "新文档")

        state = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertEqual(state.ai_guide_doc_id, new_id)
        self.assertEqual(client.docs[new_id]["markdown"], "新文档")
        self.assertEqual(client.docs[old_id]["hpath"], "/AI 使用指南")
        self.assertNotIn((old_id, "用户个性化要求"), client.renames)

    def test_registry_id_is_primary_after_feature_has_initialized(self):
        client = FakeSystemClient()
        ensure_agent_notebook(client, self.root, "zh-CN")
        registry = self._workspace_state()
        guide_id = registry["documents"]["mcp_usage_guide"]["id"]
        client.docs[guide_id]["hpath"] = "/用户改过的指南标题"

        second = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertEqual(second.mcp_usage_guide_doc_id, guide_id)
        self.assertEqual(
            len([doc for doc in client.docs.values() if "MCP 使用指南" in doc["hpath"]]),
            0,
        )

    def test_user_modified_managed_guide_is_not_overwritten(self):
        client = FakeSystemClient()
        first = ensure_agent_notebook(client, self.root, "zh-CN")
        guide_id = first.mcp_usage_guide_doc_id
        client.docs[guide_id]["markdown"] += "\n\n用户补充规则"
        update_count = len(client.updates)

        second = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertIn("用户补充规则", second.mcp_usage_guide_markdown)
        self.assertEqual(len(client.updates), update_count)
        entry = self._workspace_state()["documents"]["mcp_usage_guide"]
        self.assertTrue(entry["user_modified"])

    def test_unmodified_managed_guide_updates_across_template_versions(self):
        client = FakeSystemClient()
        first = ensure_agent_notebook(client, self.root, "zh-CN")
        guide_id = first.mcp_usage_guide_doc_id
        template_path = self.root / "templates/system-docs/mcp-usage-guide.zh-CN.md"
        new_markdown = template_path.read_text(encoding="utf-8") + "\n\n新增开发者说明。\n"
        template_path.write_text(new_markdown, encoding="utf-8", newline="\n")
        manifest_path = self.root / "templates/system-docs/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = manifest["templates"]["mcp_usage_guide"]
        entry["version"] = 2
        entry["source_sha256"]["zh-CN"] = hashlib.sha256(
            new_markdown.encode("utf-8")
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        second = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertEqual(second.mcp_usage_guide_doc_id, guide_id)
        self.assertIn("新增开发者说明", second.mcp_usage_guide_markdown)
        registry_entry = self._workspace_state()["documents"]["mcp_usage_guide"]
        self.assertEqual(registry_entry["template_version"], 2)
        self.assertFalse(registry_entry["user_modified"])

    def test_stale_registry_id_falls_back_to_current_name_and_repairs_registry(self):
        client = FakeSystemClient()
        state = ensure_agent_notebook(client, self.root, "zh-CN")
        state_path = self.root / "knowledge_base/system_state.json"
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        key = payload["active_workspace_key"]
        payload["workspaces"][key]["documents"]["ai_guide"]["id"] = "missing-id"
        state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        repaired = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertEqual(repaired.ai_guide_doc_id, state.ai_guide_doc_id)
        self.assertEqual(
            self._workspace_state()["documents"]["ai_guide"]["id"],
            state.ai_guide_doc_id,
        )

    def test_about_user_edit_is_overwritten_without_recreating_document(self):
        client = FakeSystemClient()
        first = ensure_agent_notebook(client, self.root, "zh-CN")
        about_id = first.about_doc_id
        client.docs[about_id]["markdown"] = "用户写入的重要内容"

        second = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertEqual(second.about_doc_id, about_id)
        self.assertIn("会被系统覆盖", client.docs[about_id]["markdown"])

    def test_existing_workspace_index_is_never_overwritten(self):
        client = FakeSystemClient()
        index_id = client.add_doc("工作空间索引", "# 我的索引", updated="20260501000000")

        state = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertEqual(state.workspace_index_doc_id, index_id)
        self.assertEqual(state.workspace_index_markdown, "# 我的索引")
        self.assertFalse(state.workspace_index_is_placeholder)
        self.assertEqual(state.workspace_index_updated, "20260501000000")

    def test_placeholder_text_is_detected(self):
        client = FakeSystemClient()
        index_id = client.add_doc(
            "工作空间索引", WORKSPACE_INDEX_PLACEHOLDERS["zh-CN"]
        )

        state = ensure_agent_notebook(client, self.root, "zh-CN")

        self.assertEqual(state.workspace_index_doc_id, index_id)
        self.assertTrue(state.workspace_index_is_placeholder)


if __name__ == "__main__":
    unittest.main()
