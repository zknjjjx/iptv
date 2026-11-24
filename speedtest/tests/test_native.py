#!/usr/bin/env python3
"""Native smoke tests for speedtest/app.py.

These tests intentionally use only the standard library and local loopback
HTTP.  They never invoke refresh/testsource and therefore never run a real
speed test or make a public request.
"""
import http.client
import json
import os
import random
import runpy
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "speedtest" / "app.py"


class NativeAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="iptv-native-")
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.port = self._free_port()
        self.password_file = Path(self.tmp.name) / "password"
        self.password_file.write_text("secret-password\n", encoding="utf-8")
        self.proc = None

    def tearDown(self):
        self.stop_app()
        self.tmp.cleanup()

    @staticmethod
    def _free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def start_app(self, password_file=True, password=None, listen_host="127.0.0.1"):
        env = os.environ.copy()
        env.update({
            "PYTHONUNBUFFERED": "1",
            "DATA_DIR": str(self.data),
            "PORT": str(self.port),
            "LISTEN_HOST": listen_host,
            "INTERVAL_HOURS": "0",
            "WORKERS": "2",
            "RUN_ON_START": "false",
            # A local file source is harmless; no endpoint below triggers it.
            "SOURCES": "file:///does-not-exist.m3u",
        })
        env.pop("ADMIN_PASSWORD", None)
        env.pop("ADMIN_PASSWORD_FILE", None)
        if password_file:
            env["ADMIN_PASSWORD_FILE"] = str(self.password_file)
        elif password is not None:
            env["ADMIN_PASSWORD"] = password
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(APP)], cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            if self.proc.poll() is not None:
                output = self.proc.stdout.read()
                self.fail("app exited during startup: %r\n%s" % (self.proc.returncode, output))
            try:
                status, _, _ = self.request("GET", "/api/status")
                if status == 200:
                    return
            except (ConnectionError, OSError):
                pass
            time.sleep(0.05)
        self.fail("app did not become ready")

    def stop_app(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        if self.proc is not None and self.proc.stdout is not None:
            self.proc.stdout.close()
        self.proc = None

    def request(self, method, path, body=None, token=None, origin=None,
                host=None, raw_body=None):
        headers = {"Host": host or ("127.0.0.1:%d" % self.port)}
        if token is not None:
            headers["X-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        data = raw_body if raw_body is not None else (
            json.dumps(body).encode() if body is not None else None)
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request("http://127.0.0.1:%d%s" % (self.port, path), data=data,
                      headers=headers, method=method)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def json_request(self, method, path, **kwargs):
        code, headers, body = self.request(method, path, **kwargs)
        return code, json.loads(body.decode() or "{}"), headers

    def oversized_request(self, path, token):
        """Advertise an oversized body without sending it; app must reject early."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        conn.putrequest("POST", path)
        conn.putheader("Host", "127.0.0.1:%d" % self.port)
        conn.putheader("X-Token", token)
        conn.putheader("Content-Length", str(1024 * 1024 + 1))
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        headers = dict(response.headers)
        conn.close()
        return response.status, headers, body

    def test_password_status_and_authentication(self):
        self.start_app()
        code, status, _ = self.json_request("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertTrue(status["need_password"])

        code, _, _ = self.json_request("GET", "/api/config")
        self.assertEqual(code, 401)
        code, _, _ = self.json_request("GET", "/api/report")
        self.assertEqual(code, 401)
        code, _, _ = self.json_request("POST", "/api/config", body={})
        self.assertEqual(code, 401)
        code, cfg, _ = self.json_request("GET", "/api/config", token="secret-password")
        self.assertEqual(code, 200)
        self.assertEqual(cfg["interval_hours"], 0)
        self.assertEqual(cfg["workers"], 2)
        self.assertFalse(cfg["run_on_start"])

    def test_authenticated_config_write_and_restart_persistence(self):
        self.start_app()
        update = {"interval_hours": 0, "workers": 2, "run_on_start": False,
                  "blacklist": "local-test"}
        code, result, _ = self.json_request("POST", "/api/config",
                                             token="secret-password", body=update)
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(result["config"]["blacklist"], "local-test")
        (self.data / "best.m3u").write_text("#EXTM3U\n#EXTINF:-1,Local\nfile:///local.ts\n", encoding="utf-8")
        (self.data / "best.txt").write_text("Local,file:///local.ts\n", encoding="utf-8")
        (self.data / "state.json").write_text(json.dumps({"channels": 1, "alive": 1}), encoding="utf-8")
        self.stop_app()
        self.start_app()
        code, cfg, _ = self.json_request("GET", "/api/config", token="secret-password")
        self.assertEqual(code, 200)
        self.assertEqual(cfg["blacklist"], "local-test")
        code, status, _ = self.json_request("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(status["channels"], 1)
        self.assertEqual(self.request("GET", "/m3u")[0], 200)
        self.assertEqual(self.request("GET", "/txt")[0], 200)

    def test_subscriptions_are_public_and_initially_unavailable(self):
        self.start_app()
        self.assertEqual(self.request("GET", "/m3u")[0], 503)
        self.assertEqual(self.request("GET", "/txt")[0], 503)
        (self.data / "best.m3u").write_text("#EXTM3U\n#EXTINF:-1,Local\nfile:///local.ts\n", encoding="utf-8")
        (self.data / "best.txt").write_text("Local,file:///local.ts\n", encoding="utf-8")
        code, _, body = self.request("GET", "/m3u")
        self.assertEqual(code, 200)
        self.assertIn(b"#EXTM3U", body)
        self.assertEqual(self.request("GET", "/txt")[0], 200)

    def test_origin_body_limit_and_get_token(self):
        self.start_app()
        code, _, _ = self.request("POST", "/api/config", token="secret-password",
                                  origin="http://evil.example", body={})
        self.assertEqual(code, 403)
        code, _, _ = self.oversized_request("/api/config", "secret-password")
        self.assertEqual(code, 413)
        self.assertEqual(self.request("GET", "/api/config", token="secret-password")[0], 200)

    def test_no_password_is_docker_compatible(self):
        self.start_app(password_file=False, listen_host="0.0.0.0")
        code, status, _ = self.json_request("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertFalse(status["need_password"])
        self.assertEqual(self.request("GET", "/api/config")[0], 200)

    def test_native_config_permissions(self):
        self.start_app()
        self.assertEqual((self.data / "config.json").stat().st_mode & 0o777, 0o600)

    def test_small_python_without_tzset(self):
        env = dict(os.environ, DATA_DIR=str(self.data), TZ="Asia/Unavailable")
        code = "import runpy,time; del time.tzset; runpy.run_path(%r)" % str(APP)
        p = subprocess.run([sys.executable, "-c", code], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_password_file_missing_and_empty_fail_fast(self):
        self.password_file.unlink()
        env = os.environ.copy()
        env.update({"DATA_DIR": str(self.data), "PORT": str(self.port),
                    "LISTEN_HOST": "127.0.0.1", "ADMIN_PASSWORD_FILE": str(self.password_file)})
        p = subprocess.run([sys.executable, str(APP)], cwd=str(ROOT), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3)
        self.assertNotEqual(p.returncode, 0)
        self.password_file.write_text("\n", encoding="utf-8")
        p = subprocess.run([sys.executable, str(APP)], cwd=str(ROOT), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=3)
        self.assertNotEqual(p.returncode, 0)

    def test_parse_m3u_and_txt_without_network(self):
        old_data = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = str(self.data)
        try:
            ns = runpy.run_path(str(APP))
        finally:
            if old_data is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = old_data
        parse = ns["parse"]
        m3u = '#EXTM3U\n#EXTINF:-1 tvg-logo="logo" group-title="News",Demo\nhttp://local/demo.ts\n'
        self.assertEqual(parse(m3u), [("Demo", "News", "logo", "http://local/demo.ts")])
        txt = "News,#genre#\nDemo,http://local/demo.ts#rtmp://local/live\n"
        self.assertEqual(parse(txt), [("Demo", "News", "", "http://local/demo.ts"),
                                      ("Demo", "News", "", "rtmp://local/live")])

    def test_retest_can_stop_and_cannot_queue_refresh(self):
        old_data = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = str(self.data)
        try:
            ns = runpy.run_path(str(APP))
        finally:
            if old_data is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = old_data
        class FakeHandler:
            headers = {}
            path = "/api/refresh"
            def _authed(self): return True
            def _json(self, value, code=200): return value
        ns["state"]["status"] = "复测 1 条"
        self.assertTrue(ns["is_busy"]())
        handler = FakeHandler()
        self.assertFalse(ns["H"].do_POST(handler)["ok"])
        self.assertFalse(ns["refresh_evt"].is_set())
        handler.path = "/api/stop"
        self.assertTrue(ns["H"].do_POST(handler)["ok"])
        self.assertTrue(ns["stop_evt"].is_set())
        self.assertEqual(ns["state"]["status"], "正在停止")


if __name__ == "__main__":
    unittest.main()
