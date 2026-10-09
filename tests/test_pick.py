"""Hop thoai chon file chay trong tien trinh con (mdf2sql.pick), voi tkinter gia (tests/fake_tk).

Khong mo cua so nao: tien trinh con nap tkinter gia nho PYTHONPATH tro vao tests/fake_tk.
"""

import json
import os
import subprocess
import threading
import unittest
from unittest import mock

import tests
from mdf2sql import server
from tests.test_server import post

FAKE_TK = os.path.join(tests.HERE, "fake_tk")


class PickTest(unittest.TestCase):
    def setUp(self):
        self.log = os.path.join(tests.TMP, "tk_" + self.id().rsplit(".", 1)[-1] + ".jsonl")
        if os.path.exists(self.log):
            os.remove(self.log)
        patch = mock.patch.dict(os.environ, {"PYTHONPATH": FAKE_TK, "FAKE_TK_LOG": self.log})
        patch.start()
        self.addCleanup(patch.stop)
        for k in ("FAKE_TK_MISSING", "FAKE_TK_OPEN", "FAKE_TK_SAVE", "FAKE_TK_RAISE"):
            os.environ.pop(k, None)

    def events(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_open_unicode_path_tk_on_main_thread(self):
        want = "D:\\Dữ liệu khách\\giữ xe.mdf"
        os.environ["FAKE_TK_OPEN"] = want
        self.assertEqual(server._pick_file("mdf", ""), (want, ""))
        ev = self.events()
        self.assertEqual([e["event"] for e in ev], ["Tk", "withdraw", "attributes", "askopenfilename", "destroy"])
        self.assertTrue(all(e["main_thread"] for e in ev), "moi lenh Tk phai chay tren luong chinh cua tien trinh con")
        self.assertNotEqual(ev[0].get("pid"), os.getpid())

    def test_save_passes_current_path(self):
        os.environ["FAKE_TK_SAVE"] = "echo"
        cur = "D:\\Sao lưu\\giữ xe.sql"
        self.assertEqual(server._pick_file("sql", cur), (cur, ""))
        save = [e for e in self.events() if e["event"] == "asksaveasfilename"][0]
        self.assertEqual((save["initialdir"], save["initialfile"], save["defaultextension"]),
                         ("D:\\Sao lưu", "giữ xe.sql", ".sql"))

    def test_save_without_current_uses_default_name(self):
        os.environ["FAKE_TK_SAVE"] = "echo"
        self.assertEqual(server._pick_file("sql", ""), ("database.sql", ""))

    def test_cancel_returns_empty_without_error(self):
        self.assertEqual(server._pick_file("mdf", ""), ("", ""))

    def test_unknown_kind_opens_mdf_dialog(self):
        os.environ["FAKE_TK_OPEN"] = "C:\\a.mdf"
        self.assertEqual(server._pick_file("../../x", ""), ("C:\\a.mdf", ""))

    def test_missing_tkinter_reports_error(self):
        os.environ["FAKE_TK_MISSING"] = "1"
        path, err = server._pick_file("mdf", "")
        self.assertEqual(path, "")
        self.assertIn("tkinter", err)

    def test_tcl_error_reports_error_and_destroys_root(self):
        os.environ["FAKE_TK_RAISE"] = "1"
        path, err = server._pick_file("mdf", "")
        self.assertEqual(path, "")
        self.assertIn("TclError", err)
        self.assertEqual(self.events()[-1]["event"], "destroy")

    def test_child_crash_and_timeout(self):
        crashed = subprocess.CompletedProcess([], 3, stdout=b"Fatal Python error: Tcl_AsyncDelete\r\n", stderr=b"")
        with mock.patch.object(server.subprocess, "run", return_value=crashed):
            path, err = server._pick_file("mdf", "")
        self.assertEqual(path, "")
        self.assertIn("3", err)
        with mock.patch.object(server.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertEqual(server._pick_file("mdf", "")[0], "")

    def test_parallel_http_threads(self):
        """Nhieu luong phuc vu HTTP goi cung luc: lan luot, khong tien trinh nao sap."""
        os.environ["FAKE_TK_OPEN"] = "C:\\b.mdf"
        out = []
        ts = [threading.Thread(target=lambda: out.append(server._pick_file("mdf", ""))) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertEqual(out, [("C:\\b.mdf", "")] * 4)

    def test_api_pick_route(self):
        httpd = server.bind_server(0)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            base = "http://127.0.0.1:%d" % httpd.server_address[1]
            code, _ = post(base, "/api/pick", {"kind": "mdf"})
            self.assertEqual(code, 403, "/api/pick can ma")
            os.environ["FAKE_TK_OPEN"] = "C:/du lieu/c.mdf"
            code, body = post(base, "/api/pick", {"kind": "mdf"}, server.TOKEN)
            self.assertEqual((code, body), (200, {"path": "C:\\du lieu\\c.mdf"}))
            os.environ["FAKE_TK_MISSING"] = "1"
            code, body = post(base, "/api/pick", {"kind": "mdf"}, server.TOKEN)
            self.assertEqual(code, 500)
            self.assertIn("tkinter", body["error"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(10)


if __name__ == "__main__":
    unittest.main()
