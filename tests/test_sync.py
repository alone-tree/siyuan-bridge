from __future__ import annotations

from copy import deepcopy
import json
import unittest

from source_code.client import SiYuanApiError, SiYuanConnectionError, SiYuanTimeoutError
from source_code.sync import NativeSync


class FakeClient:
    """仅记录内存调用；禁止测试接触真实思源同步。"""

    def __init__(self, *, mode=0, state=None):
        self.conf = {"readonly": False, "repo": {"key": "fake-key"},
                     "sync": {"enabled": True, "mode": mode}}
        self.state = deepcopy(state) if state is not None else {"pending": False, "source": "", "localID": "", "cloudID": ""}
        self.info = {"syncing": True, "stat": "原生状态", "native": {"unchanged": 1}}
        self.calls = []
        self.errors = {}
        self.perform_result = {"code": 0, "msg": "同步完成"}

    def _call(self, name):
        self.calls.append(name)
        if name in self.errors:
            raise self.errors[name]

    def get_conf(self):
        self._call("conf")
        return self.conf

    def get_sync_source_state(self):
        self._call("source")
        return self.state

    def perform_sync(self, *, timeout=10, upload=None, source_state=None):
        self.calls.append(("perform", timeout, upload, deepcopy(source_state)))
        if "perform" in self.errors:
            raise self.errors["perform"]
        return self.perform_result

    def get_sync_info(self):
        self._call("info")
        return self.info


def pending_state():
    return {"pending": True, "source": "source-A", "localID": "local-A", "cloudID": "cloud-A",
            "localFiles": 8, "cloudFiles": 4, "cloudCreated": 123, "cloudDevice": "Fake Linux",
            "uploadFiles": 3, "overwriteFiles": 2, "removeFiles": 1}


class NativeSyncTests(unittest.TestCase):
    def test_setup_guides_do_not_probe_or_sync_or_return_secrets(self):
        for field, reason in [("readonly", "readonly"), ("key", "repo_key_missing"), ("enabled", "sync_disabled")]:
            with self.subTest(field=field):
                client = FakeClient()
                if field == "readonly":
                    client.conf[field] = True
                else:
                    client.conf["repo" if field == "key" else "sync"][field] = "" if field == "key" else False
                result = NativeSync().run(client, upload=True)
                self.assertEqual(result["status"], "requires_setup")
                self.assertEqual(result["reason"], reason)
                self.assertTrue(result["message"])
                self.assertNotIn("fake-key", json.dumps(result))
                self.assertEqual(client.calls, ["conf"])

    def test_ordinary_modes_ignore_upload_and_use_empty_body_contract(self):
        for mode in (0, 1, 2):
            for upload in (None, True, False):
                with self.subTest(mode=mode, upload=upload):
                    client = FakeClient(mode=mode)
                    result = NativeSync().run(client, upload=upload, timeout=17)
                    self.assertEqual(client.calls, ["conf", "source", ("perform", 17, None, None), "info"])
                    self.assertEqual(result, {"status": "requested", "sync_info": client.info})

    def test_empty_cloud_upload_has_priority_even_in_mode_three(self):
        for mode in (0, 3):
            for upload in (None, True, False):
                with self.subTest(mode=mode, upload=upload):
                    state = {**pending_state(), "pending": False, "cloudID": ""}
                    client = FakeClient(mode=mode, state=state)
                    NativeSync().run(client, upload=upload)
                    self.assertEqual(client.calls, ["conf", "source", ("perform", 10, True,
                                     {"source": "source-A", "localID": "local-A", "cloudID": ""}), "info"])

    def test_pending_first_call_always_displays_preview(self):
        for mode in (0, 1, 2, 3):
            for upload in (None, True, False):
                with self.subTest(mode=mode, upload=upload):
                    client = FakeClient(mode=mode, state=pending_state())
                    result = NativeSync().run(client, upload=upload)
                    self.assertEqual(result["status"], "requires_choice")
                    self.assertEqual(result["source_state"], client.state)
                    json.dumps(result, ensure_ascii=False)
                    self.assertEqual(client.calls, ["conf", "source"])

    def test_pending_confirmation_pins_original_preview_for_both_directions(self):
        for upload in (True, False):
            with self.subTest(upload=upload):
                client = FakeClient(state=pending_state())
                sync = NativeSync()
                result = sync.run(client)
                result["source_state"]["source"] = "caller-mutated"
                client.state.update(source="source-B", localID="local-B", cloudID="cloud-B")
                client.conf["sync"]["mode"] = 3
                client.errors["source"] = AssertionError("不能重新获取用户已确认的预览")
                sync.run(client, upload=upload)
                self.assertEqual(client.calls, ["conf", "source", "conf", ("perform", 10, upload,
                                 {"source": "source-A", "localID": "local-A", "cloudID": "cloud-A"}), "info"])

    def test_no_choice_keeps_same_preview_without_new_probe(self):
        client = FakeClient(state=pending_state())
        sync = NativeSync()
        first = sync.run(client)
        client.state["localID"] = "new-local"
        for _ in range(2):
            self.assertEqual(sync.run(client), first)
        self.assertEqual(client.calls, ["conf", "source", "conf", "conf"])

    def test_mode_three_nonpending_only_sends_upload_after_preview(self):
        for upload in (True, False):
            with self.subTest(upload=upload):
                client = FakeClient(mode=3, state={**pending_state(), "pending": False})
                sync = NativeSync()
                self.assertEqual(sync.run(client, upload=upload)["status"], "requires_choice")
                sync.run(client, upload=upload)
                self.assertEqual(client.calls[-2:], [("perform", 10, upload, None), "info"])
                self.assertEqual(client.calls.count("source"), 1)

    def test_reset_cancels_preview_and_requires_new_choice(self):
        client = FakeClient(state=pending_state())
        sync = NativeSync()
        sync.run(client)
        sync.reset()
        client.state["localID"] = "new-local"
        result = sync.run(client, upload=False)
        self.assertEqual(result["status"], "requires_choice")
        self.assertEqual(result["source_state"]["localID"], "new-local")
        self.assertEqual(client.calls, ["conf", "source", "conf", "source"])

    def test_client_instance_switch_discards_preview_even_for_equal_clients(self):
        class EqualClient(FakeClient):
            def __eq__(self, other):
                return True
        old, new = EqualClient(state=pending_state()), EqualClient(state=pending_state())
        new.state["source"] = "source-B"
        sync = NativeSync()
        sync.run(old)
        self.assertEqual(sync.run(new, upload=False)["source_state"]["source"], "source-B")
        self.assertEqual(sync.run(old, upload=False)["status"], "requires_choice")
        self.assertFalse(any(isinstance(call, tuple) for call in old.calls + new.calls))

    def test_setup_block_clears_pending_preview(self):
        client = FakeClient(state=pending_state())
        sync = NativeSync()
        sync.run(client)
        client.conf["readonly"] = True
        self.assertEqual(sync.run(client, upload=False)["status"], "requires_setup")
        client.conf["readonly"] = False
        self.assertEqual(sync.run(client, upload=False)["status"], "requires_choice")
        self.assertEqual(client.calls.count("source"), 2)

    def test_only_http_404_and_405_use_legacy_mode_flow(self):
        for status in (404, 405):
            for mode in (0, 1, 2, 3):
                with self.subTest(status=status, mode=mode):
                    client = FakeClient(mode=mode)
                    client.errors["source"] = SiYuanApiError("missing endpoint", status=status)
                    sync = NativeSync()
                    result = sync.run(client, upload=False)
                    if mode == 3:
                        self.assertEqual(result["status"], "requires_choice")
                        self.assertEqual(result["source_state"], {})
                        sync.run(client, upload=False)
                        self.assertEqual(client.calls[-2:], [("perform", 10, False, None), "info"])
                    else:
                        self.assertEqual(result["status"], "requested")
                        self.assertEqual(client.calls[-2:], [("perform", 10, None, None), "info"])
                    self.assertEqual(client.calls.count("source"), 1)

    def test_source_failures_propagate_without_sync_or_fallback(self):
        errors = [SiYuanApiError("HTTP failure", status=status) for status in (400, 401, 403, 409, 500)]
        errors += [SiYuanApiError("business failure", code=404), SiYuanConnectionError("offline"),
                   SiYuanTimeoutError("slow"), RuntimeError("unexpected")]
        for error in errors:
            with self.subTest(error=error):
                client = FakeClient()
                client.errors["source"] = error
                with self.assertRaises(type(error)) as raised:
                    NativeSync().run(client)
                self.assertIs(raised.exception, error)
                self.assertEqual(client.calls, ["conf", "source"])

    def test_config_errors_propagate_without_probe(self):
        client = FakeClient()
        error = SiYuanApiError("config missing", status=404)
        client.errors["conf"] = error
        with self.assertRaises(SiYuanApiError) as raised:
            NativeSync().run(client)
        self.assertIs(raised.exception, error)
        self.assertEqual(client.calls, ["conf"])

    def test_perform_failures_never_retry_and_preserve_preview(self):
        for error in (SiYuanApiError("preview changed", code=1), SiYuanConnectionError("offline"), SiYuanTimeoutError("slow")):
            with self.subTest(error=error):
                client = FakeClient(state=pending_state())
                sync = NativeSync()
                preview = sync.run(client)
                client.errors["perform"] = error
                with self.assertRaises(type(error)) as raised:
                    sync.run(client, upload=False)
                self.assertIs(raised.exception, error)
                self.assertEqual(sum(isinstance(c, tuple) for c in client.calls), 1)
                self.assertNotIn("info", client.calls)
                self.assertEqual(sync.run(client), preview)
                self.assertEqual(client.calls.count("source"), 1)

    def test_success_consumes_choice_before_info_query(self):
        client = FakeClient(state=pending_state())
        sync = NativeSync()
        sync.run(client)
        client.errors["info"] = SiYuanTimeoutError("info slow")
        with self.assertRaises(SiYuanTimeoutError):
            sync.run(client, upload=False)
        self.assertEqual(sync.run(client, upload=True)["status"], "requires_choice")
        self.assertEqual(client.calls.count("source"), 2)
        self.assertEqual(sum(isinstance(c, tuple) for c in client.calls), 1)

    def test_result_only_reports_native_info_not_completion_markers(self):
        for info in ({}, {"syncing": True, "stat": "同步完成"}, {"syncing": False, "stat": "Failed"}):
            with self.subTest(info=info):
                client = FakeClient()
                client.info = info
                self.assertEqual(NativeSync().run(client), {"status": "requested", "sync_info": info})

    def test_completed_choice_is_not_reused_by_next_sync(self):
        client = FakeClient(state=pending_state())
        sync = NativeSync()
        sync.run(client)
        sync.run(client, upload=True)
        self.assertEqual(sync.run(client, upload=True)["status"], "requires_choice")
        self.assertEqual(client.calls.count("source"), 2)

    def test_invalid_upload_does_not_access_client(self):
        for upload in (0, 1, "false", [], {}):
            with self.subTest(upload=upload):
                client = FakeClient()
                with self.assertRaises(ValueError):
                    NativeSync().run(client, upload=upload)
                self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
