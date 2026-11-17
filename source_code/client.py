from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib import error, parse, request


Transport = Callable[[request.Request, float], Any]


class SiYuanConnectionError(RuntimeError):
    pass


class SiYuanTimeoutError(SiYuanConnectionError):
    pass


class SiYuanApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, code: int | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class SiYuanClient:
    base_url: str
    token: str | None = None
    timeout: float = 5.0
    transport: Transport | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def version(self) -> str:
        data = self._post("/api/system/version", {})
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("version", "ver"):
                if key in data:
                    return str(data[key])
        return str(data)

    def list_notebooks(self) -> list[dict[str, Any]]:
        data = self._post("/api/notebook/lsNotebooks", {})
        if isinstance(data, dict):
            notebooks = data.get("notebooks", [])
        else:
            notebooks = data
        if not isinstance(notebooks, list):
            raise SiYuanApiError("Unexpected notebooks response shape")
        return [item for item in notebooks if isinstance(item, dict)]

    def open_notebook(self, notebook_id: str) -> None:
        self._post("/api/notebook/openNotebook", {"notebook": notebook_id})

    def close_notebook(self, notebook_id: str) -> None:
        self._post("/api/notebook/closeNotebook", {"notebook": notebook_id})

    def query_sql(self, stmt: str) -> list[dict[str, Any]]:
        data = self._post("/api/query/sql", {"stmt": stmt})
        if not isinstance(data, list):
            raise SiYuanApiError("Unexpected SQL response shape")
        return [item for item in data if isinstance(item, dict)]

    def query_sql_with_info(self, stmt: str) -> dict[str, Any]:
        """Read-only search, retaining SiYuan's upstream limit/truncation metadata."""
        envelope = self._post(
            "/api/query/sql", {"stmt": stmt, "mode": "readonly"}, include_envelope=True,
        )
        data = envelope.get("data")
        if not isinstance(data, list):
            raise SiYuanApiError("Unexpected SQL response shape")
        return {**envelope, "data": [item for item in data if isinstance(item, dict)]}

    def export_markdown(self, block_id: str) -> str:
        data = self._post(
            "/api/export/exportMdContent",
            {"id": block_id, "refMode": 0, "embedMode": 0},
        )
        if isinstance(data, dict):
            for key in ("content", "markdown", "md", "kramdown"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
        if isinstance(data, str):
            return data
        raise SiYuanApiError("Unexpected markdown export response shape")

    def get_block_kramdown(self, block_id: str) -> str:
        data = self._post("/api/block/getBlockKramdown", {"id": block_id})
        if isinstance(data, dict):
            value = data.get("kramdown")
            if isinstance(value, str):
                return value
        if isinstance(data, str):
            return data
        raise SiYuanApiError("Unexpected kramdown response shape")

    def get_asset(self, asset_path: str) -> bytes:
        """Download an asset file from SiYuan's HTTP asset server."""
        parts = asset_path.lstrip("/").split("/")
        encoded = "/".join(parse.quote(p, safe="") for p in parts)
        url = f"{self.base_url}/{encoded}"
        req = request.Request(url, method="GET")
        req.add_header("Connection", "close")
        if self.token:
            req.add_header("Authorization", f"Token {self.token}")
        try:
            opener = self.transport or request.urlopen
            with opener(req, timeout=self.timeout) as response:
                return response.read()
        except error.HTTPError as exc:
            raise SiYuanApiError(f"Asset not found: {asset_path}", status=exc.code) from exc
        except error.URLError as exc:
            raise SiYuanConnectionError(str(exc.reason)) from exc

    def insert_local_assets(
        self,
        document_id: str,
        asset_paths: list[str],
        *,
        is_upload: bool = True,
    ) -> dict[str, str]:
        data = self._post(
            "/api/asset/insertLocalAssets",
            {
                "id": document_id,
                "assetPaths": asset_paths,
                "isUpload": is_upload,
            },
        )
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected insertLocalAssets response shape")
        succ_map = data.get("succMap")
        if not isinstance(succ_map, dict):
            raise SiYuanApiError("Unexpected insertLocalAssets succMap response shape")
        return {
            str(source): str(resolved)
            for source, resolved in succ_map.items()
            if str(source) and str(resolved)
        }

    def insert_local_assets_result(
        self,
        document_id: str,
        asset_paths: list[str],
        *,
        is_upload: bool = False,
    ) -> dict[str, Any]:
        """保留原生逐项结果，包括业务失败时已成功的资源。"""
        return self._post(
            "/api/asset/insertLocalAssets",
            {"id": document_id, "assetPaths": asset_paths, "isUpload": is_upload},
            include_envelope=True,
            allow_asset_errors=True,
            retry=False,
        )

    def upload_assets(self, document_id: str, file_paths: list[str]) -> dict[str, Any]:
        """发送文件内容；资源目录、命名和去重均由思源处理，不重试上传。"""
        boundary = "siyuan-bridge-" + uuid.uuid4().hex
        prefix = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="id"\r\n\r\n'
            f'{document_id}\r\n'
        ).encode("utf-8")
        suffix = f"--{boundary}--\r\n".encode("ascii")
        with ExitStack() as stack:
            files = []
            content_length = len(prefix) + len(suffix)
            for file_path in file_paths:
                path = Path(file_path)
                stream = stack.enter_context(path.open("rb"))
                size = os.fstat(stream.fileno()).st_size
                filename = (path.name.replace("\\", "\\\\").replace('"', '\\"')
                            .replace("\r", "%0D").replace("\n", "%0A"))
                header = (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file[]"; '
                    f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
                ).encode("utf-8")
                files.append((header, stream, size))
                content_length += len(header) + size + 2

            def chunks() -> Iterable[bytes]:
                yield prefix
                for header, stream, size in files:
                    yield header
                    remaining = size
                    while remaining:
                        chunk = stream.read(min(64 * 1024, remaining))
                        if not chunk:
                            raise OSError("上传期间文件被截短")
                        remaining -= len(chunk)
                        yield chunk
                    yield b"\r\n"
                yield suffix

            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(content_length),
                "Connection": "close",
            }
            if self.token:
                headers["Authorization"] = f"Token {self.token}"
            return self._post_once(
                "/api/asset/upload", chunks(), headers,
                include_envelope=True, allow_asset_errors=True,
            )

    def search_full_text(
        self,
        query: str,
        *,
        method: int = 0,
        types: dict[str, bool] | None = None,
        paths: list[str] | None = None,
        group_by: int = 1,
        order_by: int = 7,
        page: int = 1,
        page_size: int = 64,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "method": method,
            "groupBy": group_by,
            "orderBy": order_by,
            "page": page,
            "pageSize": page_size,
        }
        if types:
            payload["types"] = types
        if paths:
            payload["paths"] = paths

        data = self._post("/api/search/fullTextSearchBlock", payload)
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected search response shape")
        return data

    def list_document_blocks(self, doc_id: str) -> list[dict[str, Any]]:
        """Return all non-document blocks for a document."""
        stmt = (
            f"SELECT id, parent_id, root_id, type, subtype, markdown, content, sort "
            f"FROM blocks WHERE root_id = '{doc_id}' AND type != 'd' "
            f"ORDER BY parent_id, sort"
        )
        data = self.query_sql(stmt)
        if not isinstance(data, list):
            raise SiYuanApiError("Unexpected blocks response shape")
        return data

    def list_block_references(self, block_ids: list[str]) -> list[dict[str, Any]]:
        """Return normalized refs and siyuan:// block links targeting *block_ids*."""
        normalized = list(dict.fromkeys(str(block_id).strip() for block_id in block_ids if str(block_id).strip()))
        if not normalized:
            return []

        target_ids = set(normalized)
        rows: list[dict[str, Any]] = []
        for start in range(0, len(normalized), 200):
            chunk = normalized[start:start + 200]
            quoted = ", ".join("'" + block_id.replace("'", "''") + "'" for block_id in chunk)
            ref_stmt = (
                "SELECT r.def_block_id, r.block_id, r.root_id, r.type, "
                "b.content, b.markdown, b.type AS block_type "
                "FROM refs r LEFT JOIN blocks b ON b.id = r.block_id "
                f"WHERE r.def_block_id IN ({quoted})"
            )
            rows.extend(self.query_sql(ref_stmt))

            link_conditions = " OR ".join(
                "instr(s.markdown, 'siyuan://blocks/"
                + block_id.replace("'", "''")
                + "') > 0"
                for block_id in chunk
            )
            link_stmt = (
                "SELECT s.block_id, s.root_id, s.markdown AS span_markdown, "
                "b.content, b.markdown, b.type AS block_type "
                "FROM spans s LEFT JOIN blocks b ON b.id = s.block_id "
                f"WHERE {link_conditions}"
            )
            for row in self.query_sql(link_stmt):
                span_markdown = str(row.get("span_markdown") or "")
                for match in re.finditer(r"siyuan://blocks/([0-9A-Za-z-]+)", span_markdown, re.IGNORECASE):
                    target_id = match.group(1)
                    if target_id not in target_ids:
                        continue
                    rows.append({
                        "def_block_id": target_id,
                        "block_id": str(row.get("block_id") or ""),
                        "root_id": str(row.get("root_id") or ""),
                        "type": "block-link",
                        "content": row.get("content"),
                        "markdown": row.get("markdown"),
                        "block_type": row.get("block_type"),
                    })

        deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            target_id = str(row.get("def_block_id") or "")
            source_block_id = str(row.get("block_id") or "")
            if target_id in target_ids and source_block_id:
                deduplicated.setdefault((target_id, source_block_id), row)
        return list(deduplicated.values())

    def list_forward_block_references(self, block_ids: list[str]) -> list[dict[str, Any]]:
        """Return refs and block links originating in *block_ids*, with target blocks."""
        normalized = list(dict.fromkeys(str(block_id).strip() for block_id in block_ids if str(block_id).strip()))
        if not normalized:
            return []

        source_ids = set(normalized)
        rows: list[dict[str, Any]] = []
        for start in range(0, len(normalized), 200):
            chunk = normalized[start:start + 200]
            quoted = ", ".join("'" + block_id.replace("'", "''") + "'" for block_id in chunk)
            rows.extend(self.query_sql(
                "SELECT r.def_block_id, r.block_id, r.root_id, r.type "
                f"FROM refs r WHERE r.block_id IN ({quoted})"
            ))
            for row in self.query_sql(
                "SELECT s.block_id, s.root_id, s.markdown AS span_markdown "
                f"FROM spans s WHERE s.block_id IN ({quoted}) "
                "AND instr(lower(s.markdown), 'siyuan://blocks/') > 0"
            ):
                for match in re.finditer(
                    r"siyuan://blocks/([0-9A-Za-z-]+)",
                    str(row.get("span_markdown") or ""),
                    re.IGNORECASE,
                ):
                    rows.append({
                        "def_block_id": match.group(1),
                        "block_id": str(row.get("block_id") or ""),
                        "root_id": str(row.get("root_id") or ""),
                        "type": "block-link",
                    })

        deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            target_id = str(row.get("def_block_id") or "")
            source_block_id = str(row.get("block_id") or "")
            if target_id and source_block_id in source_ids:
                deduplicated.setdefault((target_id, source_block_id), row)

        # Resolve both standard refs and links through live blocks. Missing targets
        # remain in totals, but have no visible document or content to disclose.
        targets: dict[str, dict[str, Any]] = {}
        target_ids = list(dict.fromkeys(key[0] for key in deduplicated))
        for start in range(0, len(target_ids), 200):
            chunk = target_ids[start:start + 200]
            quoted = ", ".join("'" + block_id.replace("'", "''") + "'" for block_id in chunk)
            for block in self.query_sql(
                f"SELECT id, root_id, content, markdown, type FROM blocks WHERE id IN ({quoted})"
            ):
                targets[str(block.get("id") or "")] = block
        for row in deduplicated.values():
            target = targets.get(str(row.get("def_block_id") or ""), {})
            row.update({
                "target_root_id": target.get("root_id") or "",
                "target_content": target.get("content"),
                "target_markdown": target.get("markdown"),
                "target_block_type": target.get("type"),
            })
        return list(deduplicated.values())

    def get_child_blocks(self, block_id: str) -> list[dict[str, Any]]:
        data = self._post("/api/block/getChildBlocks", {"id": block_id})
        if not isinstance(data, list):
            raise SiYuanApiError("Unexpected child blocks response shape")
        return [item for item in data if isinstance(item, dict)]

    def delete_block(self, block_id: str) -> None:
        self._post("/api/block/deleteBlock", {"id": block_id})

    def create_snapshot(self, memo: str) -> dict[str, Any]:
        data = self._post("/api/repo/createSnapshot", {"memo": memo})
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected snapshot response shape")
        return data

    def get_repo_snapshots(self, *, page: int = 1) -> dict[str, Any]:
        data = self._post("/api/repo/getRepoSnapshots", {"page": page})
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected repo snapshots response shape")
        return data

    def get_conf(self) -> dict[str, Any]:
        data = self._post("/api/system/getConf", {})
        if not isinstance(data, dict) or not isinstance(data.get("conf"), dict):
            raise SiYuanApiError("Unexpected getConf response shape")
        return data["conf"]

    def get_sync_source_state(self) -> dict[str, Any]:
        data = self._post("/api/sync/getSyncSourceState", {}, retry=False)
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected sync source state response shape")
        return data

    def perform_sync(
        self,
        *,
        timeout: float = 10.0,
        upload: bool | None = None,
        source_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if upload is not None:
            payload["upload"] = upload
        if source_state is not None:
            payload.update({key: source_state[key] for key in ("source", "localID", "cloudID")})
        data = self._post("/api/sync/performSync", payload, timeout=timeout, retry=False)
        if data is None:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def get_sync_info(self) -> dict[str, Any]:
        data = self._post("/api/sync/getSyncInfo", {})
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected sync info response shape")
        return data

    def create_notebook(self, name: str) -> dict[str, Any]:
        data = self._post("/api/notebook/createNotebook", {"name": name})
        if data is None:
            return {}
        if isinstance(data, str):
            for nb in self.list_notebooks():
                if str(nb.get("name", "")) == name:
                    return {"id": str(nb.get("id", ""))}
            return {}
        if not isinstance(data, dict):
            return {}
        notebook = data.get("notebook")
        if isinstance(notebook, dict):
            return notebook
        return data

    def create_doc_with_md(self, notebook: str, path: str, markdown: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "notebook": notebook,
            "path": path,
            "markdown": markdown,
        }
        data = self._post("/api/filetree/createDocWithMd", payload)
        if data is None:
            return {}
        if isinstance(data, str):
            return {"id": data}
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected createDocWithMd response shape")
        return data

    def get_shorthands(self, page: int) -> dict[str, Any]:
        """收集箱分页列表。内核会把每条的正文一并返回。"""
        data = self._post("/api/inbox/getShorthands", {"page": page})
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected getShorthands response shape")
        # 内核把 apicontract.ShorthandsData 又包了一层 code/msg/data，这里摊平。
        inner = data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("shorthands"), list):
            return inner
        return data

    def get_shorthand(self, shorthand_id: str) -> dict[str, Any]:
        data = self._post("/api/inbox/getShorthand", {"id": shorthand_id})
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected getShorthand response shape")
        return data

    def remove_shorthands(self, ids: list[str]) -> None:
        self._post("/api/inbox/removeShorthands", {"ids": ids})

    def create_doc_by_parent(
        self,
        notebook: str,
        parent_id: str | None,
        doc_id: str,
        title: str,
        markdown: str,
    ) -> dict[str, Any]:
        """身份式建文档，思源 v3.8.9 起提供。parent_id 为 None 表示笔记本根。"""
        payload: dict[str, Any] = {
            "notebook": notebook,
            "parentID": parent_id,
            "id": doc_id,
            "title": {"format": "plain", "text": title},
            "markdown": markdown,
        }
        data = self._post("/api/filetree/createDocByParent", payload)
        if not isinstance(data, dict):
            raise SiYuanApiError("Unexpected createDocByParent response shape")
        return data

    def get_hpath_by_id(self, block_id: str) -> str:
        data = self._post("/api/filetree/getHPathByID", {"id": block_id})
        if isinstance(data, str):
            return data
        raise SiYuanApiError("Unexpected getHPathByID response shape")

    def rename_doc_by_id(self, doc_id: str, title: str) -> dict[str, Any]:
        data = self._post("/api/filetree/renameDocByID", {"id": doc_id, "title": title})
        if data is None:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def remove_doc_by_id(self, doc_id: str) -> dict[str, Any]:
        data = self._post("/api/filetree/removeDocByID", {"id": doc_id})
        if data is None:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def move_docs_by_id(self, doc_ids: list[str], target_id: str) -> dict[str, Any]:
        data = self._post("/api/filetree/moveDocsByID", {"fromIDs": doc_ids, "toID": target_id})
        if data is None:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def duplicate_doc(self, doc_id: str) -> dict[str, Any]:
        data = self._post("/api/filetree/duplicateDoc", {"id": doc_id})
        if data is None:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def update_block(self, block_id: str, markdown: str, ial: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": block_id,
            "dataType": "markdown",
            "data": markdown,
        }
        if ial is not None:
            payload["ial"] = ial
        data = self._post("/api/block/updateBlock", payload)
        if data is None:
            return {}
        if isinstance(data, list):
            return {"blocks": data}
        if not isinstance(data, dict):
            return {}
        return data

    def set_block_attrs(self, block_id: str, attrs: dict[str, str]) -> dict[str, Any]:
        result = self._post("/api/attr/setBlockAttrs", {"id": block_id, "attrs": attrs})
        if not result:
            return {}
        return result

    def get_attribute_view(self, av_id: str) -> dict[str, Any]:
        result = self._post("/api/av/getAttributeView", {"id": av_id})
        if not result:
            return {}
        return result.get("av", {})

    def append_block(self, parent_id: str, markdown: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dataType": "markdown",
            "data": markdown,
            "parentID": parent_id,
        }
        data = self._post("/api/block/appendBlock", payload)
        if data is None:
            return {}
        if isinstance(data, list):
            return {"blocks": data}
        if not isinstance(data, dict):
            return {}
        return data

    def insert_block_after(self, previous_id: str, markdown: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dataType": "markdown",
            "data": markdown,
            "previousID": previous_id,
        }
        data = self._post("/api/block/insertBlock", payload)
        if data is None:
            return {}
        if isinstance(data, list):
            return {"blocks": data}
        if not isinstance(data, dict):
            return {}
        return data

    def insert_block_before(self, next_id: str, markdown: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dataType": "markdown",
            "data": markdown,
            "nextID": next_id,
        }
        data = self._post("/api/block/insertBlock", payload)
        if data is None:
            return {}
        if isinstance(data, list):
            return {"blocks": data}
        if not isinstance(data, dict):
            return {}
        return data

    def extension_copy(self, dom: str, href: str, *, clip_type: str = "article", assets: bool = False) -> str:
        """内核 lute 把已抓好的 HTML 片段转成 Markdown。

        `/api/extension/copy` 自 v3.0.0 起存在，不在官方 API 文档内，属内核内部接口；
        内核自己的 MCP 与浏览器扩展都在用它。multipart 表单，返回 `data.md`。
        """
        boundary = "siyuan-bridge-" + uuid.uuid4().hex
        fields = (("dom", dom), ("href", href), ("clipType", clip_type),
                  ("assets", "true" if assets else "false"))
        chunks: list[bytes] = []
        for name, value in fields:
            chunks.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
            )
            chunks.append(value.encode("utf-8"))
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode("ascii"))
        body = b"".join(chunks)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "Connection": "close",
        }
        if self.token:
            headers["Authorization"] = f"Token {self.token}"
        data = self._post_once("/api/extension/copy", body, headers, include_envelope=True)
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            raise SiYuanApiError("Unexpected extension/copy response shape")
        markdown = payload.get("md")
        if not isinstance(markdown, str):
            raise SiYuanApiError("extension/copy 未返回 md 字段")
        return markdown

    def net_img2_local_assets(self, doc_id: str) -> None:
        """把文档内的网络图片下载到本地资源库并改写引用。

        返回 `{"code":0,"msg":"","data":null}`，没有逐项结果。
        """
        self._post("/api/format/netImg2LocalAssets", {"id": doc_id}, timeout=120.0, retry=False)

    def push_msg(self, msg: str, timeout: int = 7000) -> None:
        self._post("/api/notification/pushMsg", {"msg": msg, "timeout": timeout})

    def push_err_msg(self, msg: str, timeout: int = 7000) -> None:
        self._post("/api/notification/pushErrMsg", {"msg": msg, "timeout": timeout})

    def _post(self, path: str, payload: dict[str, Any], *, timeout: float | None = None, include_envelope: bool = False, allow_asset_errors: bool = False, retry: bool = True) -> Any:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Connection": "close"}
        if self.token:
            headers["Authorization"] = f"Token {self.token}"
        request_timeout = self.timeout if timeout is None else timeout

        last_error: Exception | None = None
        attempts = 2 if retry else 1
        for attempt in range(attempts):
            try:
                return self._post_once(path, body, headers, timeout=request_timeout, include_envelope=include_envelope, allow_asset_errors=allow_asset_errors)
            except SiYuanConnectionError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    import time
                    time.sleep(0.3)
            else:
                break
        raise last_error  # type: ignore[misc]

    def _post_once(self, path: str, body: bytes | Iterable[bytes], headers: dict[str, str], *, timeout: float | None = None, include_envelope: bool = False, allow_asset_errors: bool = False) -> Any:
        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            opener = self.transport or request.urlopen
            with opener(req, timeout=timeout if timeout is not None else self.timeout) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            message = _read_http_error(exc)
            raise SiYuanApiError(message, status=exc.code) from exc
        except error.URLError as exc:
            raise SiYuanConnectionError(str(exc.reason)) from exc
        except TimeoutError as exc:
            raise SiYuanTimeoutError("Request timed out") from exc
        except OSError as exc:
            raise SiYuanConnectionError(str(exc)) from exc

        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SiYuanApiError("SiYuan returned non-JSON response") from exc

        if not isinstance(envelope, dict):
            raise SiYuanApiError("SiYuan returned unexpected response shape")

        code = envelope.get("code", 0)
        preserve_asset_error = allow_asset_errors and path in {"/api/asset/upload", "/api/asset/insertLocalAssets"}
        if code != 0 and not preserve_asset_error:
            msg = envelope.get("msg") or envelope.get("message") or f"API returned code {code}"
            raise SiYuanApiError(str(msg), code=int(code) if isinstance(code, int) else None)

        return envelope if include_envelope else envelope.get("data")


def _read_http_error(exc: error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8")
    except Exception:
        raw = ""
    return raw or f"HTTP {exc.code}"
