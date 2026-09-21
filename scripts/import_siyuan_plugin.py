from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

import sync_siyuan_plugin_bridge


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLUGIN = ROOT / "siyuan-plugin"
PLUGIN_NAME = "siyuan-bridge"
PROTECTED_FILES = {"config.local.json", "telemetry.json"}
LEGACY_RUNTIME_FILES = {
    Path("bridge/config.local.json"): "config.local.json",
    Path("bridge/telemetry.json"): "telemetry.json",
    Path("bridge/knowledge_base/system_state.json"): "system_state.json",
    Path("bridge/knowledge_base/privacy_rules.json"): "privacy_rules.json",
}


def _ignore_config(_directory: str, files: list[str]) -> list[str]:
    """Skip protected config files during copy so existing ones stay untouched."""
    return [f for f in files if f in PROTECTED_FILES]


def resolve_plugins_dir(path: Path) -> Path:
    candidates = [
        path / "data" / "plugins",
        path / "workspace" / "data" / "plugins",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def resolve_plugin_data_dir(plugins_dir: Path) -> Path:
    resolved_plugins_dir = plugins_dir.resolve()
    if resolved_plugins_dir.name.lower() != "plugins" or resolved_plugins_dir.parent.name.lower() != "data":
        raise SystemExit(f"Cannot locate SiYuan data directory from: {resolved_plugins_dir}")
    return resolved_plugins_dir.parent / "storage" / "petal" / PLUGIN_NAME


def migrate_legacy_runtime_data(target: Path, plugin_data_dir: Path) -> None:
    """Copy old runtime data into petal storage without overwriting it."""
    for legacy_relative, storage_name in LEGACY_RUNTIME_FILES.items():
        source = target / legacy_relative
        destination = plugin_data_dir / storage_name
        if source.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    legacy_stats = target / "bridge" / "stats"
    storage_stats = plugin_data_dir / "stats"
    if legacy_stats.is_dir():
        for source in legacy_stats.rglob("*"):
            if not source.is_file():
                continue
            destination = storage_stats / source.relative_to(legacy_stats)
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def remove_target(target: Path, plugins_dir: Path) -> None:
    resolved_target = target.resolve()
    resolved_plugins_dir = plugins_dir.resolve()
    if (
        resolved_target.parent != resolved_plugins_dir
        or resolved_target.name.casefold() != PLUGIN_NAME.casefold()
    ):
        raise SystemExit(f"Refusing to remove unexpected path: {resolved_target}")
    if resolved_target.exists():
        shutil.rmtree(resolved_target)


def remove_plugin_data(plugin_data_dir: Path, plugins_dir: Path) -> None:
    resolved_data_dir = plugin_data_dir.resolve()
    expected_parent = (plugins_dir.resolve().parent / "storage" / "petal").resolve()
    if resolved_data_dir.parent != expected_parent:
        raise SystemExit(f"Refusing to remove unexpected path: {resolved_data_dir}")
    if resolved_data_dir.exists():
        shutil.rmtree(resolved_data_dir)


def verify_import(target: Path, plugin_data_dir: Path, fresh: bool) -> None:
    required = [
        "plugin.json",
        "index.js",
        "dist/index.js",
        "src/index.js",
        "bridge/source_code/mcp_server.py",
        "bridge/scripts/run_mcp.py",
        "bridge/templates/system-docs/manifest.json",
    ]
    for relative in required:
        path = target / relative
        if not path.exists():
            raise SystemExit(f"Missing after import: {path}")
    config_path = target / "bridge" / "config.local.json"
    if fresh and config_path.exists():
        raise SystemExit(f"Fresh import should not keep legacy config: {config_path}")
    if fresh and plugin_data_dir.exists():
        raise SystemExit(f"Fresh import should not keep plugin data: {plugin_data_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import siyuan-plugin into a SiYuan test workspace.")
    parser.add_argument(
        "--workspace",
        default=os.environ.get("SIYUAN_TEST_WORKSPACE", ""),
        help="SiYuan workspace root. Also accepts a parent containing workspace/data/plugins.",
    )
    parser.add_argument(
        "--plugin-dir",
        default=os.environ.get("SIYUAN_TEST_PLUGIN_DIR", ""),
        help="Explicit target plugin directory. Overrides --workspace.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Simulate first install: delete existing plugin first, no config preserved.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.workspace and not args.plugin_dir:
        raise SystemExit("Pass --workspace or set SIYUAN_TEST_WORKSPACE.")

    sync_siyuan_plugin_bridge.main()

    if args.plugin_dir:
        target = Path(args.plugin_dir).resolve()
        plugins_dir = target.parent
    else:
        plugins_dir = resolve_plugins_dir(Path(args.workspace))
        target = plugins_dir / PLUGIN_NAME

    plugin_data_dir = resolve_plugin_data_dir(plugins_dir)
    if args.fresh:
        remove_target(target, plugins_dir)
        remove_plugin_data(plugin_data_dir, plugins_dir)
    else:
        migrate_legacy_runtime_data(target, plugin_data_dir)

    plugins_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        SOURCE_PLUGIN, target,
        dirs_exist_ok=not args.fresh,
        ignore=_ignore_config if not args.fresh else None,
    )

    verify_import(target, plugin_data_dir, args.fresh)
    print(f"Imported {SOURCE_PLUGIN} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
