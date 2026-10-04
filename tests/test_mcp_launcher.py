from __future__ import annotations

import io
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from source_code import mcp_server


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "plugins/siyuan-bridge/scripts/run_mcp.py"


class McpLauncherTests(unittest.TestCase):
    def test_main_accepts_explicit_root_and_keeps_default_cwd(self):
        for root in (ROOT, None):
            with self.subTest(root=root), patch.object(mcp_server, "McpServer") as server, patch.object(
                mcp_server.sys, "stdin", io.StringIO("")
            ):
                self.assertEqual(mcp_server.main(root), 0)
                server.assert_called_once_with(root if root is not None else Path.cwd())

    def _install(self, base):
        plugin = base / "中文 workspace/data/plugins/siyuan-bridge"
        bridge = plugin / "bridge"
        (bridge / "scripts").mkdir(parents=True)
        shutil.copy2(LAUNCHER, bridge / "scripts/run_mcp.py")
        shutil.copytree(ROOT / "source_code", bridge / "source_code", ignore=shutil.ignore_patterns("__pycache__"))
        storage = plugin.parents[1] / "storage/petal/siyuan-bridge"
        storage.mkdir(parents=True)
        (storage / "config.local.json").write_text(
            json.dumps({"language": "launcher-test", "profiles": []}), encoding="utf-8"
        )
        return plugin, bridge, storage

    def test_launcher_passes_install_root_and_leaves_inherited_plugin_cwd(self):
        # Run the actual launcher with only its final main() replaced by a probe.
        with tempfile.TemporaryDirectory(prefix="bridge-launcher-") as directory:
            base = Path(directory).resolve()
            plugin, bridge, storage = self._install(base)
            probe = """
import json, runpy, sys, types
from pathlib import Path
script = Path(sys.argv[1]).absolute()
sys.path.insert(0, str(script.parents[1]))
from source_code.config import load_config
from source_code.runtime_data import plugin_data_dir
module = types.ModuleType('source_code.mcp_server')
def main(root):
    config = load_config(root)
    print(json.dumps({'root': str(root), 'cwd': str(Path.cwd()),
                      'data': str(plugin_data_dir(root)), 'language': config.language}))
    return 0
module.main = main
sys.modules['source_code.mcp_server'] = module
runpy.run_path(str(script), run_name='__main__')
"""
            env = os.environ.copy()
            env.pop("SIYUAN_AGENT_LANGUAGE", None)
            env.pop("SIYUAN_TOKEN", None)
            for cwd in (base, bridge):
                with self.subTest(cwd=cwd):
                    result = subprocess.run(
                        [sys.executable, "-c", probe, str(bridge / "scripts/run_mcp.py")],
                        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    data = json.loads(result.stdout)
                    self.assertEqual(Path(data["root"]), bridge)
                    self.assertEqual(Path(data["data"]), storage)
                    self.assertEqual(data["language"], "launcher-test")
                    self.assertEqual(Path(data["cwd"]).resolve(), Path(tempfile.gettempdir()).resolve())

    def test_running_stdio_server_does_not_block_plugin_directory_rename(self):
        with tempfile.TemporaryDirectory(prefix="bridge-rename-") as directory:
            base = Path(directory).resolve()
            plugin, bridge, storage = self._install(base)
            original_config = (storage / "config.local.json").read_bytes()
            with subprocess.Popen(
                [sys.executable, str(bridge / "scripts/run_mcp.py")], cwd=bridge,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
            ) as process:
                responses = queue.Queue()
                reader = threading.Thread(target=lambda: [responses.put(line) for line in process.stdout], daemon=True)
                reader.start()

                def request(request_id, method, params=None):
                    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                                    "method": method, "params": params or {}}) + "\n")
                    process.stdin.flush()
                    response = json.loads(responses.get(timeout=20))
                    self.assertEqual(response["id"], request_id)
                    self.assertNotIn("error", response)
                    return response["result"]

                try:
                    initialized = request(1, "initialize", {"protocolVersion": "2024-11-05",
                                                            "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
                    self.assertIn("serverInfo", initialized)
                    self.assertEqual(len(request(2, "tools/list")["tools"]), 9)
                    result = request(3, "tools/call", {"name": "siyuan_list", "arguments": {}})
                    self.assertIn("siyuan_start", json.dumps(result, ensure_ascii=False))
                    # Only rename our verified disposable installation, never a user's plugin.
                    backup = plugin.parent / "backup"
                    self.assertTrue(plugin.resolve().is_relative_to(base))
                    self.assertTrue(backup.resolve().is_relative_to(base))
                    plugin.rename(backup)
                    self.assertIsNone(process.poll())
                    self.assertEqual(len(request(4, "tools/list")["tools"]), 9)
                    self.assertEqual((storage / "config.local.json").read_bytes(), original_config)
                finally:
                    process.stdin.close()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                    reader.join(timeout=5)
                self.assertEqual(process.returncode, 0, process.stderr.read())
