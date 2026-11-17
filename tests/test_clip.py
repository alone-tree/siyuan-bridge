"""剪藏：地址校验、编码判定、正文定位、清洗与 create 接入。"""
from __future__ import annotations

import io
import unittest
from unittest import mock

from source_code import clip
from source_code.clip import (
    ClipError,
    decode_body,
    extract_title,
    locate_main_container,
    normalize_doc_title,
    parse_html,
    sanitize,
    to_html,
    validate_url,
)


class ValidateUrlTests(unittest.TestCase):
    def test_rejects_non_http_scheme(self):
        with self.assertRaises(ClipError) as ctx:
            validate_url("ftp://example.com/a")
        self.assertEqual(ctx.exception.code, "validation:invalid_url")

    def test_rejects_empty(self):
        with self.assertRaises(ClipError) as ctx:
            validate_url("   ")
        self.assertEqual(ctx.exception.code, "validation:invalid_url")

    def test_rejects_loopback(self):
        with self.assertRaises(ClipError) as ctx:
            validate_url("http://127.0.0.1/admin")
        self.assertEqual(ctx.exception.code, "validation:blocked_address")

    def test_rejects_private_lan(self):
        with self.assertRaises(ClipError) as ctx:
            validate_url("http://192.168.1.10/wiki")
        self.assertEqual(ctx.exception.code, "validation:blocked_address")

    def test_rejects_localhost_name(self):
        with self.assertRaises(ClipError) as ctx:
            validate_url("http://localhost:6806/api")
        self.assertEqual(ctx.exception.code, "validation:blocked_address")

    def test_accepts_public_url(self):
        with mock.patch.object(clip.socket, "getaddrinfo", return_value=[
            (2, 1, 6, "", ("93.184.216.34", 0)),
        ]):
            self.assertEqual(validate_url("https://example.com/a"), "https://example.com/a")

    def test_rejects_ipv6_transition_teredo(self):
        with mock.patch.object(clip.socket, "getaddrinfo", return_value=[
            (10, 1, 6, "", ("2001:0000:4136:e378:8000:63bf:3fff:fdd2", 0, 0, 0)),
        ]):
            with self.assertRaises(ClipError) as ctx:
                validate_url("http://example.com/")
            self.assertEqual(ctx.exception.code, "validation:blocked_address")


class DecodeBodyTests(unittest.TestCase):
    def test_header_charset_wins(self):
        raw = "标题".encode("gbk")
        self.assertEqual(decode_body(raw, charset="gbk"), "标题")

    def test_meta_charset_when_header_missing(self):
        raw = '<meta charset="gbk"><p>中文</p>'.encode("gbk")
        self.assertIn("中文", decode_body(raw, charset=""))

    def test_default_utf8(self):
        self.assertEqual(decode_body("纯中文".encode("utf-8")), "纯中文")

    def test_bad_header_charset_falls_back_to_meta(self):
        raw = '<meta charset="utf-8"><p>中文</p>'.encode("utf-8")
        self.assertIn("中文", decode_body(raw, charset="definitely-not-a-codec"))


class LocateContainerTests(unittest.TestCase):
    def test_semantic_article(self):
        html = "<html><body><nav>menu</nav><article>" + ("正文内容" * 100) + "</article></body></html>"
        root = parse_html(html).root
        self.assertEqual(locate_main_container(root).tag, "article")

    def test_wechat_js_content_is_only_site_rule(self):
        body = "微信正文" * 200
        html = f'<html><body><div id="js_content">{body}</div></body></html>'
        root = parse_html(html).root
        node = locate_main_container(root)
        self.assertEqual(node.attrs.get("id"), "js_content")

    def test_density_fallback(self):
        body = "段落文字" * 200
        html = f"<html><body><div class='x'><div><div>{body}</div></div></div></body></html>"
        root = parse_html(html).root
        node = locate_main_container(root)
        self.assertNotEqual(node.tag, "body")

    def test_body_fallback_when_nothing_matches(self):
        html = "<html><body><p>短</p></body></html>"
        root = parse_html(html).root
        self.assertEqual(locate_main_container(root).tag, "body")


class SanitizeTests(unittest.TestCase):
    def test_only_src_href_alt_survive(self):
        html = ('<html><body><div><p style="color:red" class="c" onclick="x()">'
                '<a href="/x" title="t" data-y="1">链接</a></p></div></body></html>')
        root = parse_html(html).root
        container = locate_main_container(root)
        out = to_html(sanitize(container, "https://example.com/a/b"))
        self.assertNotIn("style=", out)
        self.assertNotIn("class=", out)
        self.assertNotIn("onclick=", out)
        self.assertNotIn("title=", out)
        self.assertIn('href="https://example.com/x"', out)

    def test_lazy_src_is_promoted(self):
        html = ('<html><body><div>' + ("正文" * 200) +
                '<img data-src="https://cdn.example.com/1.png" src="placeholder.gif"></div></body></html>')
        root = parse_html(html).root
        out = to_html(sanitize(locate_main_container(root), "https://example.com/"))
        self.assertIn('src="https://cdn.example.com/1.png"', out)
        self.assertNotIn("placeholder.gif", out)

    def test_noise_tags_removed(self):
        html = ('<html><body><div>' + ("正文" * 200) +
                '<script>bad()</script><style>.a{}</style><iframe src="//x"></iframe></div></body></html>')
        root = parse_html(html).root
        out = to_html(sanitize(locate_main_container(root), "https://example.com/"))
        self.assertNotIn("<script", out)
        self.assertNotIn("<style", out)
        self.assertNotIn("<iframe", out)

    def test_section_becomes_div(self):
        html = "<html><body><div><section>" + ("正文" * 200) + "</section></div></body></html>"
        root = parse_html(html).root
        out = to_html(sanitize(locate_main_container(root), "https://example.com/"))
        self.assertNotIn("<section", out)
        self.assertIn("<div", out)


class TitleTests(unittest.TestCase):
    def test_og_title_first(self):
        html = ('<html><head><meta property="og:title" content="OG 标题">'
                "<title>HTML 标题</title></head><body></body></html>")
        root = parse_html(html).root
        self.assertEqual(extract_title(root, "https://example.com/x"), "OG 标题")

    def test_title_tag(self):
        html = "<html><head><title>页面标题</title></head><body></body></html>"
        root = parse_html(html).root
        self.assertEqual(extract_title(root, "https://example.com/x"), "页面标题")

    def test_url_segment_fallback(self):
        html = "<html><head></head><body></body></html>"
        root = parse_html(html).root
        self.assertEqual(extract_title(root, "https://example.com/posts/hello.html"), "hello")

    def test_normalize_strips_slash_and_newlines(self):
        self.assertEqual(normalize_doc_title("A/B\nC\tD"), "ABCD")

    def test_normalize_truncates_512(self):
        self.assertEqual(len(normalize_doc_title("字" * 600)), 512)


class ClipUrlTests(unittest.TestCase):
    def _transport(self, body: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200):
        class Response(io.BytesIO):
            def __init__(self):
                super().__init__(body)
                self.status = status
                self.headers = {"Content-Type": content_type}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return lambda req, timeout=None: Response()

    def test_clip_ok(self):
        body = ('<html><head><title>测试文章</title></head><body><article>' +
                ("正文内容" * 200) + '<img data-src="https://cdn.example.com/a.png"></article></body></html>')
        with mock.patch.object(clip, "validate_url", side_effect=lambda u: u):
            page = clip.clip_url("https://example.com/a", transport=self._transport(body.encode("utf-8")))
        self.assertEqual(page.title, "测试文章")
        self.assertIn("https://cdn.example.com/a.png", page.html)

    def test_blocked_page_raises(self):
        body = "<html><body><div>环境异常，完成验证后即可继续访问</div></body></html>"
        with mock.patch.object(clip, "validate_url", side_effect=lambda u: u):
            with self.assertRaises(ClipError) as ctx:
                clip.clip_url("https://example.com/a", transport=self._transport(body.encode("utf-8")))
        self.assertEqual(ctx.exception.code, "api:clip_blocked")

    def test_empty_body_raises_no_content(self):
        body = "<html><body><div>   </div></body></html>"
        with mock.patch.object(clip, "validate_url", side_effect=lambda u: u):
            with self.assertRaises(ClipError) as ctx:
                clip.clip_url("https://example.com/a", transport=self._transport(body.encode("utf-8")))
        self.assertEqual(ctx.exception.code, "api:clip_no_content")

    def test_too_large_raises(self):
        body = b"x" * (clip.MAX_BYTES + 10)
        with mock.patch.object(clip, "validate_url", side_effect=lambda u: u):
            with self.assertRaises(ClipError) as ctx:
                clip.clip_url("https://example.com/a", transport=self._transport(body))
        self.assertEqual(ctx.exception.code, "api:clip_too_large")

    def test_non_2xx_raises(self):
        body = b"nope"
        with mock.patch.object(clip, "validate_url", side_effect=lambda u: u):
            with self.assertRaises(ClipError) as ctx:
                clip.clip_url("https://example.com/a", transport=self._transport(body, status=403))
        self.assertEqual(ctx.exception.code, "api:clip_fetch_failed")

    def test_plain_text_passthrough(self):
        with mock.patch.object(clip, "validate_url", side_effect=lambda u: u):
            page = clip.clip_url(
                "https://example.com/notes/hello.md",
                transport=self._transport("纯文本正文".encode("utf-8"), content_type="text/plain"),
            )
        self.assertEqual(page.title, "hello")
        self.assertIn("纯文本正文", page.html)


if __name__ == "__main__":
    unittest.main()
