"""doctor: driver ODBC, SQL Server, quyen, database tam, thu muc lam viec (tat ca gia lap, chi doc)."""

import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stdout

import tests  # noqa: F401  (dat pyodbc gia, thu muc tam)
from mdf2sql import attacher, dbconn, doctor


def run_doctor(**kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = doctor.run(**kw)
    return code, buf.getvalue()


def codes(out_json):
    return {i["ma"]: i["muc"] for i in json.loads(out_json)["muc"]}


class FakeCur:
    def __init__(self, role, temp):
        self.role, self.temp, self.q = role, temp, ""

    def execute(self, q):
        self.q = q
        return self

    def fetchone(self):
        if "SERVERPROPERTY" in self.q:
            return ("16.0.1000.6", "Express Edition", "C:\\", "Microsoft SQL Server 2022 gia\nx", 1)
        return self.role

    def fetchall(self):
        return [(n,) for n in self.temp]


class FakeConn:
    def __init__(self, role=(0, 0), temp=()):
        self.c = FakeCur(role, list(temp))
        self.closed = False

    def cursor(self):
        return self.c

    def close(self):
        self.closed = True


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.saved = (dbconn.pyodbc, dbconn.pyodbc.drivers, dbconn.discover_instances, dbconn.connect, doctor._registry_drivers)
        dbconn.pyodbc.drivers = lambda: ["ODBC Driver 18 for SQL Server"]
        dbconn.discover_instances = lambda: [dbconn.Instance(".\\GIA", "SQL Server (GIA)", "service", True)]
        dbconn.connect = lambda srv, timeout=30: FakeConn(role=(1, 1))
        doctor._registry_drivers = lambda: []

    def tearDown(self):
        dbconn.pyodbc, dbconn.pyodbc.drivers, dbconn.discover_instances, dbconn.connect, doctor._registry_drivers = self.saved

    def test_old_driver_only(self):
        dbconn.pyodbc.drivers = lambda: ["SQL Server"]
        code, out = run_doctor(connect=True, as_json=True)
        c = codes(out)
        self.assertEqual(code, 2)
        self.assertEqual(c.get("ODBC_QUA_CU"), "bad")
        self.assertIn("CHUA_THU_KET_NOI", c)

    def test_driver_13_is_warning_only(self):
        dbconn.pyodbc.drivers = lambda: ["ODBC Driver 13 for SQL Server", "SQL Server"]
        code, out = run_doctor(connect=False, as_json=True)
        self.assertEqual(codes(out).get("ODBC_CU"), "warn")
        self.assertEqual(code, 0)

    def test_no_driver(self):
        dbconn.pyodbc.drivers = lambda: []
        code, out = run_doctor(connect=False, as_json=True)
        self.assertEqual(code, 2)
        self.assertEqual(codes(out).get("THIEU_ODBC"), "bad")

    def test_no_pyodbc(self):
        dbconn.pyodbc = None
        code, out = run_doctor(connect=True, as_json=True)
        self.assertEqual(code, 2)
        self.assertEqual(codes(out).get("THIEU_PYODBC"), "bad")

    def test_no_sql_server(self):
        dbconn.discover_instances = lambda: []
        code, out = run_doctor(connect=True, as_json=True)
        self.assertEqual(code, 2)
        self.assertEqual(codes(out).get("THIEU_SQL"), "bad")

    def test_sql_server_stopped(self):
        dbconn.discover_instances = lambda: [dbconn.Instance(".\\SQLEXPRESS", "SQL Server (SQLEXPRESS)", "service", False)]
        code, out = run_doctor(connect=True, as_json=True)
        self.assertEqual(code, 2)
        self.assertEqual(codes(out).get("SQL_TAT"), "bad")

    def test_all_good(self):
        code, out = run_doctor(connect=True, as_json=True)
        c = codes(out)
        self.assertEqual(c.get("KET_NOI"), "ok")
        self.assertNotIn("THIEU_QUYEN_SQL", c)
        self.assertNotIn("DB_TAM_SOT", c)

    def test_no_rights_and_leftover_temp_db(self):
        fc = FakeConn(role=(0, 0), temp=["khach__m2s_ab12cd", "khach2__m2s_ef34ab"])
        dbconn.connect = lambda srv, timeout=30: fc
        code, out = run_doctor(connect=True, as_json=True)
        c = codes(out)
        self.assertEqual(code, 2)
        self.assertEqual(c.get("THIEU_QUYEN_SQL"), "bad")
        self.assertEqual(c.get("DB_TAM_SOT"), "warn")
        self.assertEqual(c.get("KET_NOI"), "ok")
        self.assertTrue(fc.closed, "phai dong ket noi sau khi kiem tra")

    def test_connect_error(self):
        def boom(srv, timeout=30):
            raise dbconn.Mdf2SqlError("Khong ket noi duoc SQL Server '" + srv + "': gia lap")
        dbconn.connect = boom
        code, out = run_doctor(connect=True, as_json=True)
        self.assertEqual(code, 2)
        self.assertEqual(codes(out).get("KHONG_KET_NOI"), "bad")

    def test_leftover_workspace(self):
        ws = os.path.join(attacher.default_workspace(), "session_20261009_1200_abcd")
        os.makedirs(ws, exist_ok=True)
        try:
            with open(os.path.join(ws, "x.mdf"), "wb") as fh:
                fh.write(b"\0" * 2048)
            code, out = run_doctor(connect=False, as_json=True)
            self.assertEqual(codes(out).get("THU_MUC_SOT"), "warn")
        finally:
            shutil.rmtree(ws, True)


class DbconnTest(unittest.TestCase):
    def test_old_driver_connect_error_suggests_odbc18(self):
        saved = (dbconn.pyodbc.drivers, dbconn.pyodbc.connect)
        try:
            dbconn.pyodbc.drivers = lambda: ["SQL Server"]

            def bad(*a, **k):
                raise dbconn.pyodbc.Error("[Microsoft][ODBC SQL Server Driver] khong ket noi (gia)")
            dbconn.pyodbc.connect = bad
            with self.assertRaises(dbconn.Mdf2SqlError) as cm:
                dbconn.connect("(localdb)\\MSSQLLocalDB")
            self.assertIn("winget install Microsoft.msodbcsql.18", str(cm.exception))
        finally:
            dbconn.pyodbc.drivers, dbconn.pyodbc.connect = saved


if __name__ == "__main__":
    unittest.main()
