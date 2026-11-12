"""图片依赖的私有安装；依据 ARCHITECTURE.md「本地图片处理」。"""
from __future__ import annotations

import hashlib
import importlib
import json
import platform
import shutil
import sys
import sysconfig
import tempfile
import threading
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .runtime_data import plugin_data_dir

PILLOW_VERSION = "13.0.0"
_lock = threading.Lock()
_prepared: dict[str, Path] = {}


def runtime_tag() -> str:
    abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        abi += "t"
    return f"{abi}-{sysconfig.get_platform()}"


def select_wheel(wheels: list[dict]) -> dict:
    if sys.implementation.name != "cpython":
        raise RuntimeError("图片处理组件目前需要 CPython 3.11 或更高版本")
    py = f"cp{sys.version_info.major}{sys.version_info.minor}"
    abi = py + ("t" if sysconfig.get_config_var("Py_GIL_DISABLED") else "")
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        plat = sysconfig.get_platform().replace("-", "_")
    elif system == "Darwin":
        plat = "macosx_11_0_arm64" if machine == "arm64" else "macosx_10_13_x86_64"
    elif system == "Linux":
        arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64" if machine in ("x86_64", "amd64") else machine
        libc, version = platform.libc_ver()
        if libc == "glibc" and tuple(int(n) for n in version.split(".")[:2]) >= (2, 27):
            plat = f"manylinux_2_27_{arch}.manylinux_2_28_{arch}"
        elif libc == "musl":
            plat = f"musllinux_1_2_{arch}"
        else:
            raise RuntimeError("当前 Linux 系统没有兼容的预编译图片组件")
    else:
        raise RuntimeError(f"当前系统 {system} 没有兼容的预编译图片组件")
    suffix = f"-{py}-{abi}-{plat}.whl"
    for wheel in wheels:
        if wheel["filename"].endswith(suffix):
            return wheel
    raise RuntimeError(f"当前 Python/系统没有兼容的图片组件：{runtime_tag()}")


def download_urls(wheel: dict) -> list[str]:
    path = urlparse(wheel["url"]).path
    return [
        wheel["url"],
        "https://pypi.tuna.tsinghua.edu.cn" + path,
        "https://mirrors.aliyun.com/pypi" + path,
    ]


def download_wheel(wheel: dict) -> bytes:
    errors = []
    for url in download_urls(wheel):
        try:
            request = Request(url, headers={"User-Agent": "siyuan-bridge", "Connection": "close"})
            with urlopen(request, timeout=15) as response:
                data = response.read(wheel["size"] + 1)
            if len(data) != wheel["size"] or hashlib.sha256(data).hexdigest() != wheel["sha256"]:
                raise RuntimeError("下载文件的大小或 SHA-256 校验失败")
            return data
        except Exception as exc:
            errors.append(f"{urlparse(url).hostname}: {exc}")
    raise RuntimeError("图片组件自动下载失败；请检查网络后重试。" + "；".join(errors))


def runtime_directory(root: Path) -> Path:
    return plugin_data_dir(root) / "deps" / "pillow" / PILLOW_VERSION / runtime_tag()


def runtime_ready(root: Path) -> bool:
    target = runtime_directory(root)
    return (target / ".ready").is_file() and (target / "PIL" / "Image.py").is_file() and any((target / "PIL").glob("_imaging.*"))


def prepare_runtime(root: Path) -> Path:
    """不调用 pip，不写系统 Python；只有固定清单中的二进制 wheel 可安装。"""
    target = runtime_directory(root)
    with _lock:
        if runtime_ready(root):
            return target
        assets_root = Path(__file__).absolute().parents[1]
        manifest = json.loads((assets_root / "templates" / "dependencies" / f"pillow-{PILLOW_VERSION}.json").read_text(encoding="utf-8"))
        wheel = select_wheel(manifest["wheels"])
        state = {"version": PILLOW_VERSION, "tag": runtime_tag(), "filename": wheel["filename"]}
        plugin_data_dir(root).mkdir(parents=True, exist_ok=True)
        (plugin_data_dir(root) / "image-runtime.json").write_text(json.dumps(state), encoding="utf-8")
        cached_wheel = plugin_data_dir(root) / "deps" / "downloads" / wheel["filename"]
        data = cached_wheel.read_bytes() if cached_wheel.is_file() else b""
        if len(data) != wheel["size"] or hashlib.sha256(data).hexdigest() != wheel["sha256"]:
            data = download_wheel(wheel)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=target.parent))
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                if sum(member.file_size for member in archive.infolist()) > 200 * 1024 * 1024:
                    raise RuntimeError("图片组件解包大小异常")
                for member in archive.infolist():
                    if "\\" in member.filename or not (staging / member.filename).resolve().is_relative_to(staging.resolve()):
                        raise RuntimeError("图片组件包含非法文件路径")
                archive.extractall(staging)
            if not (staging / "PIL" / "Image.py").is_file():
                raise RuntimeError("图片组件不完整")
            (staging / ".ready").write_text(wheel["sha256"], encoding="ascii")
            if target.exists():
                if runtime_ready(root):
                    return target
                # 只清理本模块专属、尚未准备成功的版本目录。
                if target.parent != runtime_directory(root).parent:
                    raise RuntimeError("图片组件目录异常")
                shutil.rmtree(target)
            staging.replace(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return target


def start_preparation(root: Path, client) -> None:
    """MCP 首次连接补齐插件尚无解释器记录的情况，不增加工具调用。"""
    if runtime_ready(root):
        return

    def run():
        try:
            try:
                client.push_msg("思源桥正在自动准备图片处理组件，完成后读图即可使用。", timeout=10000)
            except Exception:
                pass
            prepare_runtime(root)
            try:
                client.push_msg("思源桥图片处理组件已就绪。")
            except Exception:
                pass
        except Exception as exc:
            try:
                client.push_err_msg(f"思源桥图片组件准备失败：{exc}。下次读图会自动重试。", timeout=12000)
            except Exception:
                pass

    threading.Thread(target=run, name="siyuan-image-prepare", daemon=True).start()


def image_library(root: Path):
    key = str(root.absolute())
    if key not in _prepared:
        target = prepare_runtime(root)
        sys.path.insert(0, str(target))
        importlib.invalidate_caches()
        _prepared[key] = target
    from PIL import Image, ImageOps
    return Image, ImageOps
