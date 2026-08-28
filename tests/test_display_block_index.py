from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from source_code import mcp_server


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "display_block_index_cases.json"
JS_TEST = Path(__file__).resolve().parent / "test_block_index.js"


def load_cases() -> list[dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return list(payload["cases"])


class ChildClient:
    def __init__(self, children: dict):
        self.children = children

    def get_child_blocks(self, block_id: str):
        return list(self.children.get(block_id, []))

    def get_attribute_view(self, _av_id: str):
        return {}


class DisplayBlockIndexFixtureTests(unittest.TestCase):
    def test_python_matches_shared_fixtures(self):
        for case in load_cases():
            with self.subTest(case["name"]):
                client = ChildClient(case["children"])
                blocks = mcp_server.build_display_blocks(
                    client,
                    case["root_id"],
                    include_block_ids=True,
                )
                actual = [
                    {
                        "index": block.index,
                        "id": block.id,
                        "type": mcp_server.display_block_semantic_type(block),
                    }
                    for block in blocks
                ]
                self.assertEqual(actual, case["expected"], case["name"])

    def test_javascript_matches_shared_fixtures(self):
        completed = subprocess.run(
            ["node", str(JS_TEST)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            self.fail(
                "JS fixture test failed:\n"
                + (completed.stdout or "")
                + (completed.stderr or "")
            )


class SuperblockReferenceReadingTests(unittest.TestCase):
    def test_include_block_ids_numbers_superblock_and_children(self):
        client = ChildClient({
            "doc1": [
                {"id": "super", "type": "s", "markdown": "{{{\nA\n}}}"},
            ],
            "super": [
                {"id": "inner", "type": "p", "markdown": "A"},
            ],
        })
        blocks = mcp_server.build_display_blocks(client, "doc1", include_block_ids=True)
        self.assertEqual([block.id for block in blocks], ["super", "inner"])
        self.assertEqual(blocks[0].index, 1)
        self.assertEqual(blocks[1].index, 2)
        self.assertIn("[1] id=super type=superblock", blocks[0].markdown)
        self.assertIn("[2] id=inner type=paragraph", blocks[1].markdown)
