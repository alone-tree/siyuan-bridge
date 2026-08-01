from __future__ import annotations

import locale
import sys
from dataclasses import dataclass
from typing import Any, Iterable

SUPPORTED_LANGUAGES = ("zh-CN", "en")
DEFAULT_LANGUAGE = "zh-CN"

SYSTEM_NOTEBOOK_NAMES: dict[str, str] = {
    "zh-CN": "思源桥",
    "en": "SiYuan Bridge",
}

# Legacy notebook names — matched for backward compatibility but never created.
LEGACY_NOTEBOOK_NAMES: dict[str, str] = {
    "思源代理桥": "zh-CN",
    "SiYuan Agent Bridge": "en",
}

SYSTEM_DOC_KEYS = [
    "ai_guide",
    "mcp_usage_guide",
    "workspace_index_guide",
    "workspace_index",
    "about",
    "privacy_rules",
]

# Legacy document names matched for backward compatibility — early users may have
# notebooks/documents created under the old project name "SiYuan Agent Bridge / 思源代理桥".
# Particularly "关于Siyuan Agent Bridge" was observed in the wild (hybrid zh-CN prefix + en notebook name).
LEGACY_DOC_NAMES: dict[str, list[str]] = {
    "ai_guide": [
        "AI 使用指南",
        "AI Guide",
    ],
    "mcp_usage_guide": ["MCP使用指南"],
    "about": [
        "关于思源代理桥",
        "About SiYuan Agent Bridge",
        "关于Siyuan Agent Bridge",
    ],
}

SYSTEM_DOC_NAMES: dict[str, dict[str, str]] = {
    "ai_guide": {
        "zh-CN": "用户个性化要求",
        "en": "User Preferences",
    },
    "mcp_usage_guide": {
        "zh-CN": "MCP 使用指南",
        "en": "MCP Usage Guide",
    },
    "workspace_index_guide": {
        "zh-CN": "工作空间索引创建指南",
        "en": "Workspace Index Guide",
    },
    "workspace_index": {
        "zh-CN": "工作空间索引",
        "en": "Workspace Index",
    },
    "about": {
        "zh-CN": "关于思源桥",
        "en": "About SiYuan Bridge",
    },
    "privacy_rules": {
        "zh-CN": "隐私规则",
        "en": "Privacy Rules",
    },
}

AI_GUIDE_TEMPLATES: dict[str, str] = {
    "zh-CN": "> 请在这里写下希望 AI 长期遵循的个性化要求。\n",
    "en": "> Write the preferences you want AI to follow here.\n",
}

LEGACY_AI_GUIDE_TEMPLATES: dict[str, str] = {
    "zh-CN": (
        "# AI 使用指南\n\n"
        "此文档存储给 AI 的长期规则。你可以在这里写下偏好、重点笔记本、写作风格和限制。\n\n"
        "## 使用说明\n\n"
        "- 在思源中直接编辑本文档即可修改规则。\n"
        "- 系统刷新不会覆盖已存在的 AI 使用指南。\n"
        "- 保持简洁 —— AI 读到的是 Markdown。\n\n"
        "## 偏好与规则\n\n"
        "> TODO: 在这里添加你的长期偏好。\n"
    ),
    "en": (
        "# AI Guide\n\n"
        "This document stores long-term instructions for AI — your preferences, important notebooks, writing style, and constraints.\n\n"
        "## Usage\n\n"
        "- Edit this document directly in SiYuan to change rules.\n"
        "- System refresh will not overwrite an existing AI Guide.\n"
        "- Keep it concise — AI reads Markdown.\n\n"
        "## Preferences & Rules\n\n"
        "> TODO: Add your long-term preferences here.\n"
    ),
}

WORKSPACE_INDEX_PLACEHOLDERS: dict[str, str] = {
    "zh-CN": "用户尚未创建工作空间索引，请询问用户是否需要创建。创建方法见《工作空间索引创建指南》。\n",
    "en": "The user has not created a Workspace Index. Ask whether they want one. See the Workspace Index Guide for instructions.\n",
}

PRIVACY_RULES_TEMPLATES: dict[str, str] = {
    "zh-CN": (
        "# 隐私规则\n\n"
        "隐私规则完全由人类在本文档控制，AI 无法阅读/编辑/删除该文档。\n\n"
        "请只编辑下面两张表格。你可以新增或删除表格行，但不要新增表格，也不要编辑表头，否则会报错。\n\n"
        "**权限模型**：使用 `权限` 列控制访问权限。\n\n"
        "- `读写`（默认）：AI 可读可写（写入仍需 confirmed=true）。不填写任何值即为默认的 读写 权限。\n"
        "- `只读`：AI 只可读取、列表、搜索、复制、导出，不可创建、编辑、改名、移动、删除。\n"
        "- `隐藏`：AI 完全不可见、不可搜索、不可访问。\n\n"
        "只设置需要限制的笔记本或文档；未列出的默认为 读写。\n"
        "文档修改会在每次工具刷新时生效，你可以告诉 AI「刷新一下」。\n\n"
        "隐藏笔记本时优先填写笔记本 ID。获取方法：在笔记本列表中点击笔记本右侧三个点，选择「设置」，然后点击「复制 ID」。"
        "如果暂时不知道 ID，也可以只填写笔记本名称；若多个笔记本重名，同名笔记本都会被匹配。\n\n"
        "隐藏文档必须填写文档 ID。标题只给你自己确认，系统不会按标题匹配。\n\n"
        "## 笔记本权限\n\n"
        "| 权限 | 笔记本ID（建议填） | 笔记本名称 | 备注（可选） |\n"
        "|------|---------------------|------------|--------------|\n"
        "| 隐藏 | 20260503123456-abcdefg | 示例：私人资料 | 完全隐藏 |\n"
        "| 只读 | 20260503123456-abcdefg | 示例：参考资料 | 只读 |\n"
        "| 隐藏 |  | 示例：个人日记 | 按名称隐藏 |\n\n"
        "## 文档权限\n\n"
        "| 权限 | 文档ID（必填，不填会报错） | 标题（仅供确认） | 备注（可选） |\n"
        "|------|---------------------------|------------------|--------------|\n"
        "| 隐藏 | 20260503123456-abcdefg | 示例：未公开项目 | 完全隐藏 |\n"
        "| 只读 | 20260503123456-abcdefg | 示例：重要参考 | 只读 |\n"
    ),
    "en": (
        "# Privacy Rules\n\n"
        "This document controls which notes are hidden from AI. AI cannot read this document.\n\n"
        "Only edit the two tables below. You may add or remove rows, but do not add tables or edit table headers.\n\n"
        "**Permission model**: Use the `Permission` column to control access.\n\n"
        "- `read_write` (default): AI can read and write (writing still requires confirmed=true). Leave blank for default.\n"
        "- `read_only`: AI can read, list, search, copy/export, but cannot create/edit/rename/move/delete.\n"
        "- `hidden`: AI cannot see, search, or access the content at all.\n\n"
        "Only add rules for notebooks or documents that need restrictions; unlisted defaults to read_write.\n"
        "For notebooks, Notebook ID is preferred. To get it, click the three-dot menu next to the notebook, "
        "open Settings, and click Copy ID. If you do not know the ID yet, use Notebook Name. "
        "If multiple notebooks share the same name, all matching notebooks will be affected.\n\n"
        "Document hiding requires Document ID. Title is only for your confirmation and is not used for matching.\n\n"
        "## Notebook Permissions\n\n"
        "| Permission | Notebook ID | Notebook Name | Reason |\n"
        "|---------------|-------------------------|---------------|-----------------|\n"
        "| hidden | 20260503123456-abcdefg | Example: Private Data | Fully hidden |\n"
        "| read_only | 20260503123456-abcdefg | Example: Reference | Read-only |\n\n"
        "## Document Permissions\n\n"
        "| Permission | Document ID | Title | Reason |\n"
        "|---------------|------------------------|-------------------------------|-----------------|\n"
        "| hidden | 20260503123456-abcdefg | Example: Unpublished Project | Fully hidden |\n"
        "| read_only | 20260503123456-abcdefg | Example: Important Reference | Read-only |\n"
    ),
}

ABOUT_TEMPLATES: dict[str, str] = {
    "zh-CN": (
        "<!-- template_version: 6 -->\n\n"
        "本文档由思源桥自动维护。插件激活或模板升级时，系统会按原文档 ID 恢复标准标题并覆盖正文。请不要在这里记录重要内容。\n\n"
        "思源桥是连接思源笔记和 AI 助手的本地桥接工具。它让 AI 在隐私规则保护下阅读、搜索和维护你的知识库。\n\n"
        "## 日常使用\n\n"
        "你可以直接让 AI 查找、阅读或整理思源中的资料。需要修改笔记时，AI 会先定位目标内容；写入前自动创建思源工作空间快照。"
        "删除被其他笔记引用的块时，系统会先检查反链并默认拒绝，避免无意破坏引用。\n\n"
        "## 系统笔记本\n\n"
        "插件激活时会自动创建和维护以下文档，并在本地 JSON 中记录它们的文档 ID：\n\n"
        "- **MCP 使用指南**：补充 AI 使用思源桥时需要遵循的原则。你可以修改，也可以在插件面板重置。\n"
        "- **用户个性化要求**：你写给 AI 的长期要求。系统只确保文档存在，不覆盖正文。\n"
        "- **工作空间索引**：AI 创建的语义导航，帮助新会话快速定位资料。\n"
        "- **工作空间索引创建指南**：创建和更新索引时遵循的规则。你可以修改，也可以在插件面板重置。\n"
        "- **隐私规则**：控制哪些笔记对 AI 隐藏或只读。只有这篇文档对 AI 硬隔离。\n"
        "- **关于思源桥**：就是本文档。标题和正文由插件维护，升级时可能覆盖。\n\n"
        "除隐私规则外，系统笔记本可以像普通笔记本一样被 AI 读取和维护。\n\n"
        "更多信息请阅读项目 README、项目网站，或联系开发者。\n"
    ),
    "en": (
        "<!-- template_version: 6 -->\n\n"
        "This document is maintained by SiYuan Bridge. When the plugin is activated or its template is upgraded, "
        "the system restores the standard title and overwrites the body while preserving the document ID. "
        "Do not store important content here.\n\n"
        "SiYuan Bridge is a local bridge between SiYuan notes and AI agents. "
        "It lets AI read, search, and maintain your knowledge base under privacy rules.\n\n"
        "## Everyday use\n\n"
        "You can ask AI to find, read, or organize material in SiYuan. Before modifying notes, AI locates the target content; "
        "a SiYuan workspace snapshot is created before writing. If deleting a block would break references from other notes, "
        "backlink protection refuses the operation by default.\n\n"
        "## System notebook\n\n"
        "When activated, the plugin automatically creates and maintains the following documents and records their document IDs in a local JSON file:\n\n"
        "- **MCP Usage Guide:** additional principles for using SiYuan Bridge. You may edit it or reset it in the plugin panel.\n"
        "- **User Preferences:** your long-term instructions for AI. The system ensures that it exists but does not overwrite its body.\n"
        "- **Workspace Index:** AI-created semantic navigation for helping new sessions locate material.\n"
        "- **Workspace Index Guide:** rules for creating and updating the index. You may edit it or reset it in the plugin panel.\n"
        "- **Privacy Rules:** controls which notes are hidden or read-only. This is the only document hard-isolated from AI.\n"
        "- **About SiYuan Bridge:** this document. Its title and body are maintained by the plugin and may be overwritten during upgrades.\n\n"
        "Except for Privacy Rules, the system notebook can be read and maintained by AI like an ordinary notebook.\n\n"
        "For more details, read the project README, visit the project website, or contact the developer.\n"
    ),
}

ABOUT_TEMPLATE_VERSION_MARKER = "<!-- template_version: 6 -->"


@dataclass(frozen=True)
class LanguageConfig:
    language: str
    preferred_reply_language: str
    startup_header: str


def resolve_language(config_language: str | None = None) -> str:
    """Resolve language preference.

    Priority:
    1. Explicit config (e.g. language="zh-CN" or "en")
    2. System locale
    3. Default zh-CN (with warning)
    """
    if config_language and config_language in SUPPORTED_LANGUAGES:
        return config_language

    try:
        sys_locale = locale.getdefaultlocale()[0]
    except (ValueError, locale.Error):
        sys_locale = None
    if sys_locale:
        sys_locale_lower = sys_locale.lower()
        if sys_locale_lower.startswith("zh"):
            return "zh-CN"
        if sys_locale_lower.startswith("en"):
            return "en"

    # Phase 1: other languages fallback to English
    # But for users whose system locale is not zh/en, default to zh-CN
    return DEFAULT_LANGUAGE


def get_notebook_name(language: str) -> str:
    return SYSTEM_NOTEBOOK_NAMES.get(language, SYSTEM_NOTEBOOK_NAMES[DEFAULT_LANGUAGE])


def get_doc_name(key: str, language: str) -> str:
    names = SYSTEM_DOC_NAMES.get(key, {})
    return names.get(language, names.get(DEFAULT_LANGUAGE, key))


def get_doc_path(key: str, language: str) -> str:
    return f"/{get_doc_name(key, language)}"


def all_notebook_names() -> list[str]:
    """Return all known notebook names (current + legacy)."""
    names = list(SYSTEM_NOTEBOOK_NAMES.values())
    names.extend(LEGACY_NOTEBOOK_NAMES.keys())
    return names


def all_doc_names_for_key(key: str) -> list[str]:
    """Return all known doc names for a key (current + legacy)."""
    names = list(SYSTEM_DOC_NAMES.get(key, {}).values())
    names.extend(LEGACY_DOC_NAMES.get(key, []))
    return names


def match_doc_key(hpath: str) -> str | None:
    """Match a document hpath (e.g. '/AI Guide') to its stable key.
    Returns None if not a system document.

    Checks current names first, then legacy names for backward compatibility.
    """
    if not hpath:
        return None
    normalized = hpath.strip("/").casefold()
    for key in SYSTEM_DOC_KEYS:
        for name in SYSTEM_DOC_NAMES[key].values():
            if name.casefold() == normalized:
                return key
    # Legacy names — ensure old/hybrid doc names are still recognized
    for key, legacy_names in LEGACY_DOC_NAMES.items():
        for legacy_name in legacy_names:
            if legacy_name.casefold() == normalized:
                return key
    return None


def match_notebook_name(name: str) -> str | None:
    """Check if a notebook name matches any known system notebook name.
    Returns the language code if matched, None otherwise.

    Checks current names first, then legacy names for backward compatibility.
    """
    if not name:
        return None
    folded = name.casefold()
    for lang, nb_name in SYSTEM_NOTEBOOK_NAMES.items():
        if nb_name.casefold() == folded:
            return lang
    # Legacy names — recognize old notebook names without creating new ones
    for legacy_name, lang in LEGACY_NOTEBOOK_NAMES.items():
        if legacy_name.casefold() == folded:
            return lang
    return None


def build_language_config(language: str) -> LanguageConfig:
    if language == "en":
        return LanguageConfig(
            language="en",
            preferred_reply_language="English",
            startup_header=(
                "## Language Preference\n\n"
                "Detected user/workspace language: en\n"
                "Preferred reply language: English\n"
                "Use English by default when talking to the user, unless the user explicitly asks for another language.\n"
            ),
        )
    return LanguageConfig(
        language="zh-CN",
        preferred_reply_language="中文",
        startup_header=(
            "## 语言偏好\n\n"
            "检测到的用户/工作空间语言：zh-CN\n"
            "优先回复语言：中文\n"
            "除非用户明确要求使用其他语言，否则默认用中文回复。\n"
        ),
    )
