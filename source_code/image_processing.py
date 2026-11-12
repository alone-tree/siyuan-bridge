"""本地图片阅读副本：标准编码、无损大图、完整切片组。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from .image_runtime import image_library

STANDARD_MAX_EDGE = 2576
SLICE_OVERLAP = 32


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime: str
    width: int
    height: int
    box: tuple[int, int, int, int]

    @property
    def tokens(self) -> int:
        # 主流高分辨率视觉粗估，非账单；按每个实际发送的切片累计。
        return math.ceil(self.width / 28) * math.ceil(self.height / 28)


def slice_boxes(width: int, height: int) -> list[tuple[int, int, int, int]]:
    short, long = sorted((width, height))
    if long <= short * 3:
        return [(0, 0, width, height)]
    count = math.ceil(long / short)
    boxes = []
    for index in range(count):
        start = max(0, round(index * long / count) - (SLICE_OVERLAP if index else 0))
        end = min(long, round((index + 1) * long / count) + (SLICE_OVERLAP if index + 1 < count else 0))
        boxes.append((0, start, width, end) if height > width else (start, 0, end, height))
    return boxes


def _encode(image, fmt: str, **options) -> bytes:
    out = BytesIO()
    image.save(out, format=fmt, **options)
    return out.getvalue()


def prepare_image(data: bytes, root: Path, *, large: bool = False) -> list[ImagePart]:
    Image, ImageOps = image_library(root)
    with Image.open(BytesIO(data)) as original:
        # 动图保留全部帧，避免将首帧当作完整图片。
        if getattr(original, "is_animated", False):
            mime = Image.MIME.get(original.format)
            if mime not in {"image/png", "image/gif", "image/webp"}:
                raise ValueError("暂不支持此动画图片格式")
            return [ImagePart(data, mime, *original.size, (0, 0, *original.size))]
        original_format = original.format
        oriented = ImageOps.exif_transpose(original)
        oriented.load()
        boxes = slice_boxes(*oriented.size)
        original_mime = Image.MIME.get(original_format)
        # 大图整图直接保留原始文件；方向标签仍由支持该格式的客户端处理。
        if large and len(boxes) == 1 and original_mime in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            return [ImagePart(data, original_mime, *oriented.size, boxes[0])]
        parts = []
        for box in boxes:
            image = oriented.crop(box)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info or "A" in image.getbands() else "RGB")
            if image.mode == "RGBA" and image.getextrema()[-1] == (255, 255):
                image = image.convert("RGB")
            if not large:
                image.thumbnail((STANDARD_MAX_EDGE, STANDARD_MAX_EDGE), Image.Resampling.LANCZOS)
            candidates = [(_encode(image, "PNG"), "image/png")]
            if max(image.size) <= 16383:
                candidates.append((_encode(image, "WEBP", lossless=True, exact=True, method=6), "image/webp"))
                if not large:
                    candidates.append((_encode(image, "WEBP", quality=90, method=6), "image/webp"))
            if len(boxes) == 1 and image.size == original.size and original.getexif().get(274, 1) == 1 and original_mime in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                candidates.append((data, original_mime))
            encoded, mime = min(candidates, key=lambda item: len(item[0]))
            parts.append(ImagePart(encoded, mime, *image.size, box))
        return parts
