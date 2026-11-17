"""从 URL 剪藏网页：抓取、编码判定、正文定位与清洗。

依据 ARCHITECTURE.md「剪藏（从 URL 建文档）」。本模块只负责「拿到干净 HTML 片段」，
HTML 转 Markdown 交给思源内核，写入交给 siyuan_create 现有流程。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable
from urllib import error, parse, request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
FETCH_TIMEOUT = 30.0
MAX_BYTES = 5 * 1024 * 1024
META_SCAN_BYTES = 4096

# 反爬验证页特征词。通用特征，不是站点规则。
BLOCK_MARKERS = (
    "环境异常",
    "访问受限",
    "Just a moment",
    "Please enable JS",
    "Security Verification",
    "Sina Visitor System",
    "zh-zse-ck",
)

# 删除的噪音标签
DROP_TAGS = frozenset({
    "script", "style", "noscript", "svg", "iframe", "form",
    "button", "select", "textarea", "canvas", "template",
})

KEEP_ATTRS = frozenset({"src", "href", "alt"})
LAZY_SRC_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-actualsrc")

# 语义容器选择器，按优先级。站点专属规则只有微信 #js_content。
SEMANTIC_SELECTORS: tuple[tuple[str, str], ...] = (
    ("id", "js_content"),
    ("tag", "article"),
    ("tag", "main"),
    ("attr", "role=main"),
    ("id", "main-content"),
    ("class", "article-content"),
    ("class", "post-content"),
    ("class", "entry-content"),
    ("class", "content"),
)
MIN_CONTAINER_TEXT = 200
MIN_CONTAINER_RATIO = 0.15

VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


class ClipError(RuntimeError):
    """剪藏失败。code 用于映射 MCP 错误码。"""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)
    parent: "Node | None" = None

    def text(self) -> str:
        parts: list[str] = []
        stack: list[Node | str] = [self]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                parts.append(item)
            else:
                stack.extend(reversed(item.children))
        return "".join(parts)

    def iter(self) -> Iterable["Node"]:
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed([c for c in node.children if isinstance(c, Node)]))


class _TreeBuilder(HTMLParser):
    """标准库 html.parser 建轻量 DOM。只保留 tag/attrs/children/parent。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node(tag="#document")
        self._stack: list[Node] = [self.root]
        self.base_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        raw = {name.lower(): (value or "") for name, value in attrs}
        if tag == "base" and raw.get("href") and not self.base_href:
            self.base_href = raw["href"]
        node = Node(tag=tag, attrs=raw, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        raw = {name.lower(): (value or "") for name, value in attrs}
        node = Node(tag=tag, attrs=raw, parent=self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)


def parse_html(html_text: str) -> _TreeBuilder:
    builder = _TreeBuilder()
    builder.feed(html_text)
    builder.close()
    return builder


# ---------------------------------------------------------------------------
# 地址校验：对齐思源内核 SSRF 防护，禁止内网、回环、链路本地与 IPv6 过渡地址
# ---------------------------------------------------------------------------

def validate_url(url: str) -> str:
    """校验并归一 URL；不合格抛 ClipError。"""
    text = (url or "").strip()
    if not text:
        raise ClipError("source_url 不能为空。", code="validation:invalid_url")
    parsed = parse.urlsplit(text)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ClipError(
            f"只接受 http 与 https 网址，当前是「{parsed.scheme or '空'}」。",
            code="validation:invalid_url",
        )
    if not parsed.hostname:
        raise ClipError("网址缺少主机名，无法抓取。", code="validation:invalid_url")
    _check_public_host(parsed.hostname)
    return text


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv6 过渡地址：NAT64、6to4、Teredo
        if ip.sixtofour is not None:
            return True
        if ip.teredo is not None:
            return True
        if ip.ipv4_mapped is not None and _is_private_ip(ip.ipv4_mapped):
            return True
        if ip in ipaddress.ip_network("64:ff9b::/96"):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
            if _is_private_ip(embedded):
                return True
    return False


def _check_public_host(hostname: str) -> None:
    host = hostname.strip("[]")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ClipError(
            f"无法解析主机名「{host}」，请检查网址或网络。底层原因：{exc}",
            code="api:clip_fetch_failed",
        ) from exc
    addresses: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            addresses.add(str(sockaddr[0]))
    if not addresses:
        raise ClipError(f"主机名「{host}」没有解析到任何地址。", code="api:clip_fetch_failed")
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if _is_private_ip(ip):
            raise ClipError(
                f"「{host}」解析到内网或回环地址（{ip}），出于安全限制不允许抓取。"
                "这是地址安全限制，不是网络故障。该功能与思源内核 web_fetch 行为一致，"
                "只抓取公网网页。",
                code="validation:blocked_address",
            )


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------

@dataclass
class FetchedPage:
    url: str
    content_type: str
    text: str

    @property
    def is_html(self) -> bool:
        lowered = self.content_type.lower()
        return "html" in lowered or not lowered

    @property
    def is_plain(self) -> bool:
        lowered = self.content_type.lower()
        return lowered.startswith("text/plain") or "markdown" in lowered


def fetch_page(url: str, *, transport=None, timeout: float = FETCH_TIMEOUT) -> FetchedPage:
    """抓取网页。地址校验在请求前完成，失败不发起请求。"""
    target = validate_url(url)
    req = request.Request(target, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", ACCEPT)
    req.add_header("Accept-Language", ACCEPT_LANGUAGE)
    req.add_header("Connection", "close")
    opener = transport or request.urlopen
    try:
        with opener(req, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if not (200 <= status < 300):
                raise ClipError(
                    f"抓取失败：HTTP {status}。请确认网址可访问。",
                    code="api:clip_fetch_failed",
                )
            content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
            charset = ""
            if hasattr(response, "headers"):
                try:
                    charset = response.headers.get_content_charset() or ""
                except Exception:
                    charset = ""
            raw = response.read(MAX_BYTES + 1)
    except ClipError:
        raise
    except error.HTTPError as exc:
        raise ClipError(
            f"抓取失败：HTTP {exc.code}。请确认网址可访问，或改用浏览器扩展手动剪藏。",
            code="api:clip_fetch_failed",
        ) from exc
    except error.URLError as exc:
        raise ClipError(
            f"抓取失败，网络不可达：{exc.reason}。请检查网络或代理设置。",
            code="api:clip_fetch_failed",
        ) from exc
    except TimeoutError as exc:
        raise ClipError(
            f"抓取超时（{int(timeout)} 秒）。请检查网络或代理设置。",
            code="api:clip_fetch_failed",
        ) from exc
    except OSError as exc:
        raise ClipError(f"抓取失败：{exc}", code="api:clip_fetch_failed") from exc

    if len(raw) > MAX_BYTES:
        raise ClipError(
            f"网页超过 {MAX_BYTES // (1024 * 1024)} MiB 抓取上限，已中止。",
            code="api:clip_too_large",
        )

    text = decode_body(raw, charset=charset)
    return FetchedPage(url=target, content_type=content_type, text=text)


def decode_body(raw: bytes, *, charset: str = "") -> str:
    """网页编码判定顺序：HTTP 头 charset → HTML 前 4KB meta charset → UTF-8。

    与 markdown_file 的本地文件顺序（UTF-8 → GBK → GB18030）不同，两者不复用。
    """
    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    head = raw[:META_SCAN_BYTES]
    meta_charset = _sniff_meta_charset(head)
    if meta_charset:
        candidates.append(meta_charset)
    candidates.append("utf-8")

    seen: set[str] = set()
    for name in candidates:
        lowered = name.strip().lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        try:
            return raw.decode(lowered)
        except (UnicodeDecodeError, LookupError):
            continue
    # 全部失败时按 UTF-8 替换解码，避免整篇丢弃
    return raw.decode("utf-8", errors="replace")


_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w\-]+)""", re.IGNORECASE)


def _sniff_meta_charset(head: bytes) -> str:
    match = _META_CHARSET_RE.search(head)
    if not match:
        return ""
    try:
        return match.group(1).decode("ascii", errors="ignore")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 正文定位
# ---------------------------------------------------------------------------

def locate_main_container(root: Node) -> Node:
    """语义容器 → 文本密度 → body 兜底。"""
    body = next((node for node in root.iter() if node.tag == "body"), root)
    total_text = len(_visible_text(body).strip())

    if total_text:
        for kind, value in SEMANTIC_SELECTORS:
            for node in root.iter():
                if not _matches(node, kind, value):
                    continue
                text_len = len(_visible_text(node).strip())
                if text_len >= MIN_CONTAINER_TEXT and text_len >= total_text * MIN_CONTAINER_RATIO:
                    return node

    best = _density_scan(body)
    if best is not None and len(_visible_text(best).strip()) >= MIN_CONTAINER_TEXT:
        return best
    return body


def _matches(node: Node, kind: str, value: str) -> bool:
    if kind == "id":
        return node.attrs.get("id", "") == value
    if kind == "tag":
        return node.tag == value
    if kind == "class":
        classes = node.attrs.get("class", "").split()
        return value in classes
    if kind == "attr":
        key, _, expected = value.partition("=")
        return node.attrs.get(key, "").lower() == expected.lower()
    return False


def _density_scan(root: Node) -> Node | None:
    best: Node | None = None
    best_score = 0.0
    for node in root.iter():
        if node.tag not in {"article", "main", "div", "section"}:
            continue
        text_len = len(_visible_text(node).strip())
        if not text_len:
            continue
        link_len = 0
        for inner in node.iter():
            if inner.tag == "a":
                link_len += len(_visible_text(inner).strip())
        score = (text_len - 2 * link_len) / (1 + 0.08 * _depth(node))
        if score > best_score:
            best_score = score
            best = node
    return best


def _depth(node: Node) -> int:
    depth = 0
    current = node.parent
    while current is not None:
        depth += 1
        current = current.parent
    return depth


def _visible_text(node: Node) -> str:
    parts: list[str] = []
    stack: list[Node | str] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            parts.append(item)
        elif item.tag not in DROP_TAGS:
            stack.extend(reversed(item.children))
    return "".join(parts)


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------

def sanitize(node: Node, base_url: str) -> Node:
    """清洗：删噪音标签、只留 src/href/alt、提升懒加载图、section 改 div、相对地址补全。"""
    cleaned = _clean(node, base_url)
    if cleaned.tag != "body":
        cleaned = _wrap_body(cleaned)
    return cleaned


def _clean(node: Node, base_url: str) -> Node:
    tag = "div" if node.tag == "section" else node.tag
    attrs = _clean_attrs(tag, node.attrs, base_url)
    children: list[Node | str] = []
    for child in node.children:
        if isinstance(child, Node):
            if child.tag in DROP_TAGS:
                continue
            children.append(_clean(child, base_url))
        else:
            children.append(child)
    return Node(tag=tag, attrs=attrs, children=children)


def _clean_attrs(tag: str, attrs: dict[str, str], base_url: str) -> dict[str, str]:
    kept: dict[str, str] = {}
    for name, value in attrs.items():
        if name in KEEP_ATTRS and value:
            kept[name] = value
    if tag == "img":
        for lazy in LAZY_SRC_ATTRS:
            candidate = attrs.get(lazy, "").strip()
            if candidate:
                kept["src"] = candidate
                break
    for name in ("src", "href"):
        if name in kept:
            resolved = _absolute_url(kept[name], base_url)
            if resolved is None:
                kept.pop(name, None)
            else:
                kept[name] = resolved
    return kept


def _absolute_url(value: str, base_url: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    joined = parse.urljoin(base_url, text)
    parsed = parse.urlsplit(joined)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    return joined


def _wrap_body(node: Node) -> Node:
    body = Node(tag="body", children=[node])
    node.parent = body
    return body


def to_html(node: Node) -> str:
    parts: list[str] = []
    _serialize(node, parts)
    return "".join(parts)


def _serialize(node: Node, parts: list[str]) -> None:
    if node.tag == "#document":
        for child in node.children:
            if isinstance(child, Node):
                _serialize(child, parts)
            else:
                parts.append(_escape(child))
        return
    if node.tag == "body":
        for child in node.children:
            if isinstance(child, Node):
                _serialize(child, parts)
            else:
                parts.append(_escape(child))
        return
    attrs = "".join(f' {name}="{_escape_attr(value)}"' for name, value in node.attrs.items())
    if node.tag in VOID_TAGS:
        parts.append(f"<{node.tag}{attrs}>")
        return
    parts.append(f"<{node.tag}{attrs}>")
    for child in node.children:
        if isinstance(child, Node):
            _serialize(child, parts)
        else:
            parts.append(_escape(child))
    parts.append(f"</{node.tag}>")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(text: str) -> str:
    return _escape(text).replace('"', "&quot;")


# ---------------------------------------------------------------------------
# 标题
# ---------------------------------------------------------------------------

def extract_title(root: Node, url: str) -> str:
    """og:title → <title> → URL 末段。"""
    for node in root.iter():
        if node.tag != "meta":
            continue
        key = (node.attrs.get("property") or node.attrs.get("name") or "").lower()
        if key == "og:title":
            content = node.attrs.get("content", "").strip()
            if content:
                return content
    for node in root.iter():
        if node.tag == "title":
            text = node.text().strip()
            if text:
                return text
    parsed = parse.urlsplit(url)
    segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    segment = parse.unquote(segment)
    segment = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", segment)
    return segment.strip()


_DOC_TITLE_STRIP = str.maketrans({"\r": None, "\n": None, "\t": None, "\u2028": None, "\u2029": None, "/": None})


def normalize_doc_title(title: str) -> str:
    """按思源内核同名规则清洗网页标题。

    内核 createDocWithMd 的 documentBaseNameReplacer 会删掉换行、制表符、\\u2028、
    \\u2029 与半角 /，超过 512 rune 截断。桥必须先去掉 /，因为它在桥这一侧是
    路径层级分隔符，会在到达内核之前把标题拆成两级目录。其余字符交给内核处理。
    """
    cleaned = (title or "").translate(_DOC_TITLE_STRIP).strip()
    if len(cleaned) > 512:
        cleaned = cleaned[:512]
    return cleaned


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

@dataclass
class ClippedPage:
    url: str
    title: str
    html: str


def _looks_blocked(text: str) -> bool:
    return any(marker in text for marker in BLOCK_MARKERS)


def clip_url(url: str, *, transport=None, timeout: float = FETCH_TIMEOUT) -> ClippedPage:
    """抓取并清洗一个网页，返回用于内核转换的 HTML 片段与标题。"""
    page = fetch_page(url, transport=transport, timeout=timeout)

    if page.is_plain:
        title = normalize_doc_title(_title_from_url(page.url))
        if not title:
            raise ClipError("无法从网址推断标题，请显式传 title。", code="validation:missing_param")
        escaped = _escape(page.text)
        return ClippedPage(url=page.url, title=title, html=f"<pre>{escaped}</pre>")

    if not page.is_html:
        raise ClipError(
            f"不支持的内容类型「{page.content_type}」，本功能只处理网页、纯文本和 Markdown。",
            code="api:clip_no_content",
        )

    builder = parse_html(page.text)
    root = builder.root
    base_url = builder.base_href or page.url

    container = locate_main_container(root)
    visible = _visible_text(container).strip()

    if _looks_blocked(visible) or _looks_blocked(page.text[:META_SCAN_BYTES * 2]):
        raise ClipError(
            "该页面返回的是验证或反爬页面，正文拿不到，无法剪藏。"
            "这类页面需要登录态或浏览器验证，请改用浏览器扩展手动剪藏。",
            code="api:clip_blocked",
        )

    if not visible:
        raise ClipError(
            "该页面没有可读正文，可能由 JavaScript 渲染。"
            "请改用浏览器扩展手动剪藏。",
            code="api:clip_no_content",
        )

    cleaned = sanitize(container, base_url)
    title = normalize_doc_title(extract_title(root, page.url) or _title_from_url(page.url))
    if not title:
        raise ClipError("无法从页面推断标题，请显式传 title。", code="validation:missing_param")

    return ClippedPage(url=page.url, title=title, html=to_html(cleaned))


def _title_from_url(url: str) -> str:
    parsed = parse.urlsplit(url)
    segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    segment = parse.unquote(segment)
    segment = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", segment)
    return segment.strip()
