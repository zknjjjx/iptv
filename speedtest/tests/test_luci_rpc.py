import importlib.machinery
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).parents[1]
RPC = ROOT / "openwrt/files/usr/libexec/rpcd/iptv-speedtest"
loader = importlib.machinery.SourceFileLoader("iptv_rpc", str(RPC))
spec = importlib.util.spec_from_loader("iptv_rpc", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)


class RpcContractTests(unittest.TestCase):
    def test_list_uses_rpcd_value_type_inference(self):
        methods = mod.main(["rpc", "list"])
        self.assertEqual(methods["report"], {"offset": 0, "limit": 0, "query": ""})
        self.assertEqual(methods["testsource"], {"url": ""})
        self.assertEqual(methods["status"], {})
        self.assertNotIn("password", json.dumps(methods))

    def test_cli_call_uses_method_without_object_name(self):
        code = "import json,sys; sys.path.insert(0, %r); import runpy; runpy.run_path(%r, run_name='__main__')" % (str(RPC.parent), str(RPC))
        result = subprocess.run([sys.executable, "-c", code, "call", "unknown"], input=b"{}\n", stdout=subprocess.PIPE, check=True)
        self.assertEqual(json.loads(result.stdout), {"ok": False, "error": "unknown method"})

    @patch.object(mod, "request")
    def test_config_call_passes_stdin_params_to_method(self, request):
        request.return_value = {"config": {"enabled": True}}
        with patch.object(mod.sys, "stdin", io.StringIO('{"ignored": 1}')):
            result = mod.main(["rpcd", "call", "config"])
        self.assertEqual(result, {"ok": True, "data": {"enabled": True}})
        request.assert_called_once_with("config")

    @patch.object(mod, "request")
    def test_allowlist_and_status_metadata(self, request):
        request.return_value = {"status": "idle", "need_password": True}
        with patch.object(mod, "read_uci", return_value=("0.0.0.0", 8899)), patch.object(
            mod, "local_bind", return_value=("127.0.0.1", 8899)
        ):
            result = mod.call("status", {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["listen_host"], "127.0.0.1")
        self.assertNotIn("token", json.dumps(result))

    @patch.object(mod, "request")
    def test_report_preserves_types_and_caps_page(self, request):
        request.return_value = [
            {"name": "News", "kbps": 123, "weak": False},
            {"name": "Sports", "url": "http://x"},
        ]
        result = mod.call("report", {"query": "news", "offset": 0, "limit": 999})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["total"], 1)
        self.assertEqual(result["data"]["limit"], 100)
        self.assertEqual(result["data"]["items"], [{"name": "News", "kbps": 123, "weak": False}])

    @patch.object(mod, "request")
    def test_backend_error_is_not_wrapped_as_success(self, request):
        request.return_value = {"ok": False, "error": "scan failed"}
        self.assertEqual(mod.call("report", {}), {"ok": False, "error": "scan failed"})
        self.assertEqual(mod.call("config", {}), {"ok": False, "error": "scan failed"})

    @patch.object(mod, "request")
    def test_failed_reason_filter_counts_and_export_pagination(self, request):
        request.return_value = [
            {"name": "A", "url": "http://a", "reason": "HTTP 404"},
            {"name": "B", "url": "http://b", "reason": "timeout 5s"},
            {"name": "C", "url": "http://c", "reason": "timeout 8s"},
        ]
        answer = mod.call("failed", {"query": "", "reason": "timeout", "offset": 1, "limit": 1})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["data"]["total"], 2)
        self.assertEqual(answer["data"]["items"][0]["name"], "C")
        self.assertEqual(answer["data"]["reasons"], {"HTTP 404": 1, "timeout": 2})
        self.assertFalse(mod.call("failed", {"reason": 2})["ok"])

    def test_input_validation_does_not_echo_secret(self):
        result = mod.call("save", {"data": "not-json"})
        self.assertFalse(result["ok"])
        self.assertNotIn("not-json", json.dumps(result))
        self.assertFalse(mod.call("testsource", {"url": 3})["ok"])
        self.assertFalse(mod.call("nope", {})["ok"])

    @patch.object(mod, "read_token", side_effect=RuntimeError("backend unavailable"))
    def test_disabled_backend_is_graceful_and_does_not_leak(self, _token):
        result = mod.call("config", {})
        self.assertEqual(result, {"ok": False, "error": "backend unavailable"})

    def test_local_bind_rejects_non_local_and_maps_wildcard(self):
        self.assertEqual(mod.local_bind("0.0.0.0", 8899), ("127.0.0.1", 8899))
        with self.assertRaises(ValueError):
            mod.local_bind("192.0.2.1", 8899)
        with self.assertRaises(ValueError):
            mod.local_bind("127.0.0.1", 70000)

    def test_report_response_limit_is_16mib_but_other_limit_is_1mib(self):
        class Response:
            status = 200
            def read(self, size):
                self.size = size
                return b"{}"
        class Connection:
            response = Response()
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return self.response
            def close(self): pass
        with patch.object(mod, "local_bind", return_value=("127.0.0.1", 8899)), patch.object(mod, "read_token", return_value="x"), patch.object(mod.http.client, "HTTPConnection", Connection):
            mod.request("report")
            self.assertEqual(Connection.response.size, mod.MAX_REPORT_RESPONSE + 1)
            mod.request("config")
            self.assertEqual(Connection.response.size, mod.MAX_BODY + 1)


if __name__ == "__main__":
    unittest.main()
