"""收集箱（云端 Inbox）虚拟笔记本。

收集箱条目只存在于思源云端：本地没有副本、没有文档 ID、不在块树和全文索引里。
这里只做与云端无关的纯计算和与 SiYuanClient 协作的读取封装，格式化留给 mcp_server。
"""

from __future__ import annotations

import random
import re
import string
from datetime import datetime
from typing import Any, Iterable, Iterator

from .i18n import (
    DEFAULT_LANGUAGE,
    get_inbox_notebook_name,
    get_inbox_system_suffix,
)

MAX_INBOX_NAME_INDEX = 99

# oId 是毫秒时间戳字符串，与思源文档 ID 的 YYYYMMDDHHMMSS-xxxxxxx 不会撞。
SHORTHAND_ID_PATTERN = re.compile(r"\d{11,20}")


class InboxUnavailable(RuntimeError):
    """收集箱接口不可用：未登录、无订阅、网络异常或返回结构异常。"""


def inbox_base_name(language: str) -> str:
    return get_inbox_notebook_name(language or DEFAULT_LANGUAGE)


def inbox_notebook_name(language: str, real_names: Iterable[str]) -> str:
    """按可见笔记本名算出不冲突的虚拟笔记本名。"""
    base = inbox_base_name(language)
    system = base + get_inbox_system_suffix(language or DEFAULT_LANGUAGE)
    taken = {
        str(name).strip().casefold()
        for name in real_names
        if str(name).strip()
    }
    if base.casefold() not in taken:
        return base
    if system.casefold() not in taken:
        return system
    for index in range(2, MAX_INBOX_NAME_INDEX + 1):
        candidate = f"{system}({index})"
        if candidate.casefold() not in taken:
            return candidate
    return f"{system}({MAX_INBOX_NAME_INDEX + 1})"


def split_inbox_path(path: str, name: str) -> str | None:
    """把 /<虚拟笔记本>/<条目名> 拆成条目名。

    虚拟笔记本是单层的，只按第一个 / 拆一次，剩余的整段都是条目名。
    返回 None 表示不是收集箱路径，返回空串表示路径就是虚拟笔记本本身。
    """
    text = str(path or "").strip().strip("/")
    if not text:
        return None
    first, _, rest = text.partition("/")
    if first.casefold() != str(name or "").strip().casefold():
        return None
    return rest.strip()


def is_shorthand_id(value: str) -> bool:
    return bool(SHORTHAND_ID_PATTERN.fullmatch(str(value or "").strip()))


def entry_title(entry: dict[str, Any]) -> str:
    return str(entry.get("shorthandTitle") or "").strip()


def entry_id(entry: dict[str, Any]) -> str:
    return str(entry.get("oId") or "").strip()


def entry_markdown(entry: dict[str, Any]) -> str:
    return str(entry.get("shorthandMd") or "")


def entry_created(entry: dict[str, Any]) -> str:
    return str(entry.get("hCreated") or "").strip()


def entry_url(entry: dict[str, Any]) -> str:
    return str(entry.get("shorthandURL") or "").strip()


def iter_shorthands(client: Any) -> Iterator[dict[str, Any]]:
    """按云端分页逐条产出收集箱条目。"""
    page = 1
    while True:
        try:
            data = client.get_shorthands(page)
        except Exception as exc:  # noqa: BLE001 - 统一归为不可用
            raise InboxUnavailable(str(exc)) from exc
        if not isinstance(data, dict):
            raise InboxUnavailable("云端返回结构异常")
        pagination = data.get("pagination")
        pagination = pagination if isinstance(pagination, dict) else {}
        batch = data.get("shorthands")
        batch = [item for item in batch if isinstance(item, dict)] if isinstance(batch, list) else []
        for item in batch:
            yield item
        try:
            page_count = int(pagination.get("paginationPageCount") or 0)
        except (TypeError, ValueError):
            page_count = 0
        if not batch or page >= page_count:
            return
        page += 1


def collect_shorthands(client: Any, stop_after: int | None = None) -> tuple[list[dict[str, Any]], int]:
    """拉取条目并返回 (条目列表, 云端总数)。stop_after 只提前结束翻页，不改变总数语义。"""
    entries: list[dict[str, Any]] = []
    total = 0
    page = 1
    while True:
        try:
            data = client.get_shorthands(page)
        except Exception as exc:  # noqa: BLE001 - 统一归为不可用
            raise InboxUnavailable(str(exc)) from exc
        if not isinstance(data, dict):
            raise InboxUnavailable("云端返回结构异常")
        pagination = data.get("pagination")
        pagination = pagination if isinstance(pagination, dict) else {}
        try:
            total = int(pagination.get("paginationRecordCount") or 0)
            page_count = int(pagination.get("paginationPageCount") or 0)
        except (TypeError, ValueError):
            total, page_count = 0, 0
        batch = data.get("shorthands")
        batch = [item for item in batch if isinstance(item, dict)] if isinstance(batch, list) else []
        entries.extend(batch)
        if stop_after is not None and len(entries) >= stop_after:
            break
        if not batch or page >= page_count:
            break
        page += 1
    return entries, total


def find_entry(entries: Iterable[dict[str, Any]], locator: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """先按 oId 精确匹配，再按标题全等匹配。返回 (唯一命中, 同名候选)。"""
    wanted = str(locator or "").strip()
    if not wanted:
        return None, []
    folded = wanted.casefold()
    materialized = list(entries)
    for entry in materialized:
        if entry_id(entry) == wanted:
            return entry, []
    matches = [entry for entry in materialized if entry_title(entry).casefold() == folded]
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def title_matches(title: str, mode: str, query: str) -> bool:
    """收集箱只按标题匹配。query 模式按空白切词全命中，regex 模式用 Python 正则。"""
    text = str(title or "")
    if not text:
        return False
    if mode == "regex":
        try:
            return re.search(str(query or ""), text) is not None
        except re.error:
            return False
    terms = [term for term in str(query or "").split() if term]
    if not terms:
        return False
    folded = text.casefold()
    return all(term.casefold() in folded for term in terms)


def writable_document_title(title: str) -> str:
    """移出时写入内核的标题。

    半角 / 转全角 ／：路径式分支会把半角 / 当层级切分，转全角后两个版本分支结果一致。
    """
    text = str(title or "")
    for token in ("\r\n", "\r", "\n", "\t"):
        text = text.replace(token, " ")
    return text.replace("/", "／").strip()


def new_document_id() -> str:
    """生成与思源一致的文档 ID：YYYYMMDDHHMMSS-xxxxxxx。"""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    alphabet = string.ascii_lowercase + string.digits
    return f"{stamp}-{''.join(random.choice(alphabet) for _ in range(7))}"


def supports_create_doc_by_parent(version: str) -> bool:
    """createDocByParent 自 v3.8.9 起存在。"""
    numbers = [int(part) for part in re.findall(r"\d+", str(version or ""))[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers) >= (3, 8, 9)
