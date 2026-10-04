from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).absolute().parents[1]
# In the flattened structure, scripts/ lives directly in bridge/,
# and source_code/ is alongside scripts/ in the same bridge/ directory.
# run_mcp.py: scripts/run_mcp.py → parents[0]=scripts → parents[1]=bridge (REPO_ROOT)
REPO_ROOT = PLUGIN_ROOT

# Do not hold the plugin directory as CWD: Windows Bazaar updates rename it.
os.chdir(tempfile.gettempdir())
sys.path.insert(0, str(REPO_ROOT))
sys.stdin = io.TextIOWrapper(sys.stdin.detach(), encoding="utf-8", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from source_code.mcp_server import main


if __name__ == "__main__":
    raise SystemExit(main(REPO_ROOT))
