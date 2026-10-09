"""May chu giao dien: cong rieng, lui cong, --port 0, /api/health, /api/shutdown, server.json, CLI."""

import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout

import tests
from mdf2sql import server

DA_TAT = "Đã tắt."   # "Da tat." co dau, server in ra khi dung


def post(base, path, body, tok=None):
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["X-Token"] = tok
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


class PortTest(unittest.TestCase):
    def test_busy_port_moves_on_and_is_exclusive(self):
        a = server.bind_server(0)
        try:
            pa = a.server_address[1]
            b = server.bind_server(pa)
            try:
                self.assertNotEqual(pa, b.server_address[1], "cong dang ban: may chu thu hai phai lui sang cong khac")
            finally:
                b.server_close()
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                with self.assertRaises(OSError, msg="tien trinh khac khong duoc chiem cong dang nghe, ke ca voi SO_REUSEADDR"):
                    s.bind(("127.0.0.1", pa))
            finally:
                s.close()
        finally:
            a.server_close()


class DenyTest(unittest.TestCase):
    def test_unauthorized_post_always_gets_403(self):
        """Tu choi ma van doc het than yeu cau: khong thi Windows gui RST, ben goi mat cau tra loi 403."""
        httpd = server.bind_server(0)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            base = "http://127.0.0.1:%d" % httpd.server_address[1]
            for _ in range(20):
                code, body = post(base, "/api/convert", {"path": "x" * 32768})
                self.assertEqual(code, 403)
                self.assertIn("error", body)
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(5)


class ServeTest(unittest.TestCase):
    def test_health_shutdown_state_file(self):
        buf = io.StringIO()
        t = threading.Thread(target=lambda: server.serve(port=0, open_browser=False), daemon=True)
        with redirect_stdout(buf):      # luong may chu in ra sys.stdout chung
            t.start()
            for _ in range(100):
                if os.path.isfile(server.state_path()):
                    break
                time.sleep(0.1)
            with open(server.state_path(), encoding="utf-8") as fh:
                st = json.load(fh)
            port, token = st["port"], st["token"]
            self.assertEqual(st["pid"], os.getpid())
            self.assertTrue(st["url"].endswith("/?k=" + token) and str(port) in st["url"])
            base = "http://127.0.0.1:%d" % port

            h = json.load(urllib.request.urlopen(base + "/api/health", timeout=5))
            self.assertTrue(h["ok"])
            self.assertEqual((h["app"], h["port"], h["jobs_running"]), ("mdf2sql", port, 0))
            self.assertNotIn("token", h, "health khong duoc lo ma")

            code, _ = post(base, "/api/shutdown", {})
            self.assertEqual(code, 403, "shutdown khong co ma")
            jid = server._new_job()
            try:
                code, body = post(base, "/api/shutdown", {}, token)
                self.assertEqual((code, body["jobs_running"]), (409, 1), "dang chuyen doi thi khong tat")
                self.assertEqual(json.load(urllib.request.urlopen(base + "/api/health", timeout=5))["jobs_running"], 1)
                code, body = post(base, "/api/shutdown", {"force": True}, token)
                self.assertEqual(code, 200)
                self.assertTrue(body["ok"])
                t.join(10)
            finally:
                server._update(jid, done=True)
        self.assertFalse(t.is_alive(), "may chu phai dung")
        self.assertFalse(os.path.exists(server.state_path()), "xoa server.json khi tat")
        self.assertIn(DA_TAT, buf.getvalue())


class CliTest(unittest.TestCase):
    def env(self):
        e = dict(os.environ)
        e["PYTHONPATH"] = os.pathsep.join([tests.FAKE_DIR, tests.REPO])
        e["PYTHONIOENCODING"] = "utf-8"
        return e

    def test_gui_port_0_prints_real_url_and_stops_remotely(self):
        p = subprocess.Popen([sys.executable, "-m", "mdf2sql", "gui", "--port", "0", "--no-browser"], cwd=tests.REPO,
                             env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        try:
            url = None
            t0 = time.time()
            while time.time() - t0 < 30:
                line = p.stdout.readline()
                if not line:
                    break
                if "http://127.0.0.1:" in line:
                    url = line.strip()
                    break
            self.assertIsNotNone(url, "phai in URL ngay (flush)")
            self.assertFalse(url.startswith("http://127.0.0.1:0/"), "URL phai la cong that")
            port = int(url.split(":")[2].split("/")[0])
            tok = url.split("k=")[1]
            code, _ = post("http://127.0.0.1:%d" % port, "/api/shutdown", {}, tok)
            self.assertEqual(code, 200)
            self.assertEqual(p.wait(15), 0)
            self.assertIn(DA_TAT, p.stdout.read())
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()

    def test_bad_port(self):
        r = subprocess.run([sys.executable, "-m", "mdf2sql", "gui", "--port", "-1"], cwd=tests.REPO, env=self.env(),
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
