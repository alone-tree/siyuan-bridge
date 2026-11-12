from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from source_code import image_processing as processing, image_runtime as runtime, mcp_server as mcp

Image, ImageOps = runtime.image_library(Path(__file__).parents[1])


def encoded(image, fmt="PNG", **kwargs):
    buffer = BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


class ImageProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        codec = mock.patch.object(processing, "image_library", return_value=(Image, ImageOps))
        codec.start()
        self.addCleanup(codec.stop)

    def test_long_threshold_and_horizontal_symmetry(self):
        self.assertEqual(len(processing.slice_boxes(100, 300)), 1)
        self.assertEqual(len(processing.slice_boxes(100, 301)), 4)
        vertical = processing.slice_boxes(860, 14040)
        horizontal = processing.slice_boxes(14040, 860)
        self.assertEqual(len(vertical), 17)
        self.assertEqual([(b[1], b[0], b[3], b[2]) for b in vertical], horizontal)
        self.assertEqual(vertical[0][1], 0)
        self.assertEqual(vertical[-1][3], 14040)
        for previous, current in zip(vertical, vertical[1:]):
            self.assertLess(current[1], previous[3])
        self.assertLess(max(b[3] - b[1] for b in vertical), 900)

    def test_large_crops_preserve_every_pixel_including_alpha(self):
        source = Image.new("RGBA", (96, 600))
        source.putdata([(x % 256, y % 256, (x + y) % 256, (x * 3 + y) % 256) for y in range(600) for x in range(96)])
        parts = processing.prepare_image(encoded(source), self.root, large=True)
        self.assertEqual(len(parts), 7)
        for part in parts:
            decoded = Image.open(BytesIO(part.data)).convert("RGBA")
            self.assertEqual(decoded.tobytes(), source.crop(part.box).tobytes())
        self.assertEqual(sum(p.tokens for p in parts), sum(((p.width + 27) // 28) * ((p.height + 27) // 28) for p in parts))

    def test_large_non_long_jpeg_keeps_original_bytes(self):
        data = encoded(Image.new("RGB", (400, 200), "red"), "JPEG", quality=70)
        result = processing.prepare_image(data, self.root, large=True)
        self.assertEqual(result[0].data, data)
        self.assertEqual(result[0].mime, "image/jpeg")

    def test_standard_resizes_only_above_limit(self):
        parts = processing.prepare_image(encoded(Image.new("RGB", (3200, 2200), "white")), self.root)
        self.assertEqual(parts[0].width, processing.STANDARD_MAX_EDGE)
        self.assertLess(parts[0].height, 2200)
        small = processing.prepare_image(encoded(Image.new("RGB", (600, 300), "white")), self.root)
        self.assertEqual((small[0].width, small[0].height), (600, 300))

    def test_exif_orientation_is_applied_before_slicing(self):
        image = Image.new("RGB", (100, 400), "red")
        exif = Image.Exif()
        exif[274] = 6
        result = processing.prepare_image(encoded(image, "JPEG", exif=exif), self.root)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0].box[1], 0)
        self.assertEqual(result[-1].box[2], 400)

    def test_inline_slices_are_one_atomic_budget_group(self):
        source = encoded(Image.new("RGB", (100, 700), "blue"))
        client = mock.Mock()
        client.get_asset.return_value = source
        original = processing.prepare_image
        with mock.patch.object(mcp, "prepare_image", side_effect=lambda data, root, large=False: original(data, root, large=large)):
            text, images, stats = mcp.inline_images_into_markdown("![long](assets/a.png)", root=self.root, client=client, doc_id="doc", allow_large=False)
            self.assertEqual(len(images), 7)
            self.assertEqual(stats["inlined"], 1)
            total_bytes = stats["inlined_bytes"]
            self.assertEqual(stats["inlined_bytes"], sum(len(image["data"]) for image in images))
            self.assertIn("切片 7/7", json.dumps(mcp.build_read_content(text, images), ensure_ascii=False))
            for large in (False, True):
                _, rejected, rejected_stats = mcp.inline_images_into_markdown("![long](assets/a.png)", root=self.root, client=client, doc_id="doc", allow_large=large, budget_bytes=1)
                self.assertEqual(rejected, [])
                self.assertEqual(rejected_stats["over_budget"], 1)
            self.assertGreater(total_bytes, 1)

    def test_visual_tokens_are_reported_separately_from_text_pagination(self):
        images = [{"data": "AAAA", "mimeType": "image/png", "source": "a", "tokens": 9999}]
        text, total = mcp.format_read_result(["Header"], ["@@SIYUAN-IMAGE:0@@"], 1000, images)
        self.assertEqual(total, mcp.read_text_token_count(text, images) + 9999)
        self.assertLess(mcp.read_text_token_count(text, images), 1000)
        self.assertEqual(mcp.read_response_byte_size(text, images), len(json.dumps(mcp.make_result("x" * 64, {"content": mcp.build_read_content(text, images)}), ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1)

    def test_final_rpc_guard_counts_unicode_and_never_sends_partial_images(self):
        server = mcp.McpServer(self.root)
        server._active_client = mock.Mock()

        def read(_args):
            server._pending_read_images = [{"data": "A" * 1200, "mimeType": "image/png", "source": "original"}]
            return "中文正文" * 80 + "@@SIYUAN-IMAGE:0@@"

        with mock.patch.object(server, "siyuan_read", side_effect=read), mock.patch.object(mcp, "MCP_RESPONSE_LIMIT_BYTES", 1000):
            result = server.call_tool(1, "siyuan_read", {"document_id": "fixture"})
        self.assertTrue(result["result"]["isError"])
        self.assertFalse(any(item["type"] == "image" for item in result["result"]["content"]))
        self.assertLess(mcp.response_byte_size(result), 1000)


class ImageRuntimeTests(unittest.TestCase):
    def test_pinned_download_retries_and_rejects_bad_hash(self):
        payload = b"verified wheel"
        wheel = {"url": "https://files.pythonhosted.org/packages/a/b.whl", "filename": "b.whl", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        bad = mock.MagicMock()
        bad.__enter__.return_value.read.return_value = b"corrupt"
        good = mock.MagicMock()
        good.__enter__.return_value.read.return_value = payload
        with mock.patch.object(runtime, "urlopen", side_effect=[OSError("unavailable"), bad, good]) as fetch:
            self.assertEqual(runtime.download_wheel(wheel), payload)
            self.assertEqual(fetch.call_count, 3)
        self.assertIn("tsinghua", runtime.download_urls(wheel)[1])

    def test_platform_select_uses_actual_python_abi(self):
        manifest = json.loads((Path(__file__).parents[1] / "templates/dependencies/pillow-13.0.0.json").read_text())
        selected = runtime.select_wheel(manifest["wheels"])
        self.assertIn(f"-cp{runtime.sys.version_info.major}{runtime.sys.version_info.minor}-", selected["filename"])
        with mock.patch.object(runtime.platform, "system", return_value="Darwin"), mock.patch.object(runtime.platform, "machine", return_value="arm64"):
            self.assertTrue(runtime.select_wheel(manifest["wheels"])["filename"].endswith("macosx_11_0_arm64.whl"))
        for version, minimum in [((3, 11), "10_10"), ((3, 12), "10_13"), ((3, 14), "10_15")]:
            with mock.patch.object(runtime.sys, "version_info", mock.Mock(major=version[0], minor=version[1])), mock.patch.object(runtime.platform, "system", return_value="Darwin"), mock.patch.object(runtime.platform, "machine", return_value="x86_64"), mock.patch.object(runtime.platform, "mac_ver", return_value=("14.0", (), "")):
                self.assertTrue(runtime.select_wheel(manifest["wheels"])["filename"].endswith(f"macosx_{minimum}_x86_64.whl"))

    def test_private_install_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("PIL/Image.py", "# fixture")
                archive.writestr("PIL/_imaging.test", "fixture")
            with mock.patch.object(runtime, "download_wheel", return_value=buffer.getvalue()) as download:
                target = runtime.prepare_runtime(root)
                self.assertTrue(target.is_relative_to(root / "deps"))
                self.assertEqual(runtime.prepare_runtime(root), target)
                self.assertEqual(download.call_count, 1)

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("../escape", "x")
            with mock.patch.object(runtime, "download_wheel", return_value=buffer.getvalue()):
                with self.assertRaisesRegex(RuntimeError, "非法文件路径"):
                    runtime.prepare_runtime(Path(directory))
            self.assertFalse((Path(directory) / "escape").exists())
