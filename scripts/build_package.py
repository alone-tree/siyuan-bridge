"""Build package.zip for SiYuan marketplace submission.

Usage:  python scripts/build_package.py

Requires running sync_siyuan_plugin_bridge.py first (it's called automatically).
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import sync_siyuan_plugin_bridge

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "siyuan-plugin"
DIST = ROOT / "dist"
PACKAGE = DIST / "package.zip"

# README sync: root → plugin (marketplace reads from plugin dir)
README_SYNC = {
    ROOT / "README.md": PLUGIN / "README.zh-CN.md",
    ROOT / "README.en-US.md": PLUGIN / "README.md",
}

IMAGE_DIR = ROOT / "image" / "README"

PLUGIN_ROOT_FILES = [
    "icon.png",
    "index.css",
    "index.js",
    "plugin.json",
    "preview.png",
    "README.md",
    "README.zh-CN.md",
]

PLUGIN_ROOT_DIRS = [
    "bridge",
    "dist",
    "i18n",
    "src",
]

PACKAGE_EXTRA_DIRS = [
    (ROOT / "image" / "README", Path("image") / "README"),
]

BRIDGE_RUNTIME_NAMES = {
    "ai_workspace",
    "knowledge_base",
    "stats",
    "config.local.json",
    "telemetry.json",
}


def verify_required(paths: list[Path]) -> None:
    missing = [str(p.relative_to(ROOT)) for p in paths if not p.exists()]
    if missing:
        raise SystemExit(f"Missing required files:\n  " + "\n  ".join(missing))


def sync_readme_files() -> None:
    """Copy root README files to plugin dir for marketplace packaging.

    Root README.md (Chinese) → siyuan-plugin/README.zh-CN.md
    Root README.en-US.md (English) → siyuan-plugin/README.md
    """
    for src, dst in README_SYNC.items():
        if not src.exists():
            raise SystemExit(f"Missing README source: {src.relative_to(ROOT)}")
        shutil.copy2(src, dst)
        print(f"  {src.name} → {dst.relative_to(ROOT)}")


def verify_readme_images(*readme_paths: Path) -> None:
    """Verify all image references in README files exist in IMAGE_DIR."""
    import re

    pattern = re.compile(r"!\[.*?\]\(image/README/(.+?)\)")
    missing_images: list[str] = []
    for readme in readme_paths:
        if not readme.exists():
            continue
        for m in pattern.finditer(readme.read_text(encoding="utf-8")):
            img_name = m.group(1)
            img_path = IMAGE_DIR / img_name
            if not img_path.exists():
                missing_images.append(
                    f"  {readme.relative_to(ROOT)} → image/README/{img_name}"
                )
    if missing_images:
        raise SystemExit(
            "Missing image files referenced in README:\n" + "\n".join(missing_images)
        )


def main() -> int:
    print("Syncing bridge...")
    sync_siyuan_plugin_bridge.main()

    print("Syncing README files...")
    sync_readme_files()

    files = [PLUGIN / f for f in PLUGIN_ROOT_FILES]
    dirs = [PLUGIN / d for d in PLUGIN_ROOT_DIRS if (PLUGIN / d).exists()]

    verify_required(files + [source for source, _ in PACKAGE_EXTRA_DIRS])
    verify_readme_images(
        PLUGIN / "README.zh-CN.md",
        PLUGIN / "README.md",
    )

    DIST.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(PACKAGE, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            arcname = file_path.name
            zf.write(file_path, arcname)
            print(f"  + {arcname}")

        for dir_path in dirs:
            arcroot = dir_path.name + "/"
            for p in dir_path.rglob("*"):
                relative_parts = p.relative_to(dir_path).parts
                if (
                    dir_path.name == "bridge"
                    and relative_parts
                    and relative_parts[0] in BRIDGE_RUNTIME_NAMES
                ):
                    continue
                if p.is_file() and "__pycache__" not in p.parts:
                    arcname = str(p.relative_to(PLUGIN))
                    zf.write(p, arcname)
                elif p.is_dir():
                    pass
            print(f"  + {arcroot}*")

        for source_dir, archive_dir in PACKAGE_EXTRA_DIRS:
            for file_path in source_dir.rglob("*"):
                if file_path.is_file():
                    relative_path = file_path.relative_to(source_dir)
                    arcname = (archive_dir / relative_path).as_posix()
                    zf.write(file_path, arcname)
            print(f"  + {archive_dir.as_posix()}/*")

    size_kb = PACKAGE.stat().st_size / 1024
    print(f"\nBuilt {PACKAGE.relative_to(ROOT)} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
