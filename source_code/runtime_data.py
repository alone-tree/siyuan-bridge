from __future__ import annotations

import shutil
from pathlib import Path


PLUGIN_NAME = "siyuan-bridge"
CONFIG_FILE = "config.local.json"
TELEMETRY_FILE = "telemetry.json"
SYSTEM_STATE_FILE = "system_state.json"
PRIVACY_RULES_FILE = "privacy_rules.json"

_LEGACY_FILES = {
    CONFIG_FILE: Path(CONFIG_FILE),
    TELEMETRY_FILE: Path(TELEMETRY_FILE),
    SYSTEM_STATE_FILE: Path("knowledge_base") / SYSTEM_STATE_FILE,
    PRIVACY_RULES_FILE: Path("knowledge_base") / PRIVACY_RULES_FILE,
}


def plugin_data_dir(root: Path) -> Path:
    """Return SiYuan's persistent plugin data directory for an installed bridge.

    Development checkouts keep using their project root so existing CLI and tests do
    not need a synthetic SiYuan workspace.
    """
    resolved = root.absolute()
    plugin_dir = resolved.parent
    plugins_dir = plugin_dir.parent
    data_dir = plugins_dir.parent
    if (
        resolved.name.casefold() == "bridge"
        and plugin_dir.name.casefold() == PLUGIN_NAME
        and plugins_dir.name.casefold() == "plugins"
        and data_dir.name.casefold() == "data"
    ):
        return data_dir / "storage" / "petal" / PLUGIN_NAME
    return resolved


def runtime_data_path(root: Path, filename: str) -> Path:
    return plugin_data_dir(root) / filename


def legacy_data_path(root: Path, filename: str) -> Path:
    relative = _LEGACY_FILES.get(filename, Path(filename))
    return root.absolute() / relative


def migrate_legacy_file(root: Path, filename: str) -> Path:
    """Copy one legacy runtime file into persistent storage without overwriting it."""
    target = runtime_data_path(root, filename)
    source = legacy_data_path(root, filename)
    if target == source or target.exists() or not source.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def runtime_read_path(root: Path, filename: str) -> Path:
    """Prefer persistent storage, copying or falling back to the legacy location."""
    target = runtime_data_path(root, filename)
    if target.exists():
        return target
    source = legacy_data_path(root, filename)
    if target == source or not source.exists():
        return target
    try:
        migrate_legacy_file(root, filename)
    except OSError:
        return source
    return target if target.exists() else source


def telemetry_stats_dir(root: Path) -> Path:
    return plugin_data_dir(root) / "stats"


def migrate_legacy_stats(root: Path) -> list[Path]:
    """Copy legacy telemetry stats into persistent storage without replacing files."""
    source = root.absolute() / "stats"
    target = telemetry_stats_dir(root)
    if source == target or not source.exists():
        return []
    copied: list[Path] = []
    for source_file in source.rglob("*"):
        if not source_file.is_file():
            continue
        relative = source_file.relative_to(source)
        target_file = target / relative
        if target_file.exists():
            continue
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
        copied.append(target_file)
    return copied


def migrate_legacy_runtime_data(root: Path) -> list[Path]:
    """Best-effort one-time copy of all durable legacy runtime data."""
    migrated: list[Path] = []
    for filename in (CONFIG_FILE, TELEMETRY_FILE, SYSTEM_STATE_FILE, PRIVACY_RULES_FILE):
        target = runtime_data_path(root, filename)
        existed = target.exists()
        try:
            migrate_legacy_file(root, filename)
        except OSError:
            continue
        if not existed and target.exists():
            migrated.append(target)
    try:
        migrated.extend(migrate_legacy_stats(root))
    except OSError:
        pass
    return migrated
