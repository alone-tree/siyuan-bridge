from __future__ import annotations

from copy import deepcopy
from typing import Any

from .client import SiYuanApiError, SiYuanClient


class NativeSync:
    """复用原生同步分支，并把方向选择绑定到本实例展示的预览。"""

    def __init__(self) -> None:
        self._client: SiYuanClient | None = None
        self._pending: dict[str, Any] | None = None

    def reset(self) -> None:
        self._client = None
        self._pending = None

    def run(
        self, client: SiYuanClient, *, upload: bool | None = None, timeout: float = 10,
    ) -> dict[str, Any]:
        if upload is not None and not isinstance(upload, bool):
            raise ValueError("upload必须为布尔值，或省略以显示原生方向选择。")
        if client is not self._client:
            self.reset()
            self._client = client

        conf = client.get_conf()
        reason = message = ""
        if conf.get("readonly"):
            reason = "readonly"
            message = "思源当前处于只读模式，无法同步。请先在思源中退出只读模式，再重试。"
        elif not (conf.get("repo") or {}).get("key"):
            reason = "repo_key_missing"
            message = "尚未配置数据仓库密钥。请在思源的同步设置中完成密钥配置，再重试；不会自动初始化密钥。"
        elif not (conf.get("sync") or {}).get("enabled"):
            reason = "sync_disabled"
            message = "尚未启用同步。请在思源的同步设置中配置并启用同步，再重试。"
        if reason:
            self._pending = None
            return {"status": "requires_setup", "reason": reason, "message": message}

        # 选择必须使用用户已经看到的预览，不能重新预检替换确认对象。
        if self._pending is not None:
            if upload is None:
                return self._choice()
            state = self._pending
            source_state = self._source_ids(state) if state.get("pending") else None
            return self._perform(client, timeout, upload=upload, source_state=source_state)

        try:
            state = client.get_sync_source_state()
        except SiYuanApiError as exc:
            if exc.status not in (404, 405):
                raise
            # 旧版syncGuide：普通模式空body，mode=3先展示选择、随后仅传upload。
            state = {}

        if state.get("source") and not state.get("cloudID"):
            return self._perform(client, timeout, upload=True, source_state=self._source_ids(state))
        if not state.get("pending") and conf["sync"].get("mode") != 3:
            return self._perform(client, timeout)

        # 即便首次传了upload，也先展示原生预览；下一调用才承接明确选择。
        self._pending = deepcopy(state)
        return self._choice()

    def _choice(self) -> dict[str, Any]:
        return {
            "status": "requires_choice",
            "source_state": deepcopy(self._pending),
            "message": "请向用户展示原生同步预览，并明确选择上传（upload=true）或下载（upload=false）；选择可能覆盖或删除数据。取消时不要提交方向。",
        }

    @staticmethod
    def _source_ids(state: dict[str, Any]) -> dict[str, Any]:
        return {key: state[key] for key in ("source", "localID", "cloudID")}

    def _perform(
        self, client: SiYuanClient, timeout: float, *, upload: bool | None = None,
        source_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client.perform_sync(timeout=timeout, upload=upload, source_state=source_state)
        self._pending = None
        return {"status": "requested", "sync_info": client.get_sync_info()}
