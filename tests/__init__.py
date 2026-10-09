"""Bai thu mdf2sql (unittest, khong can pyodbc that, khong can SQL Server).

Chay:  python -m unittest discover -s tests -t .      (trong thu muc mdf2sql)

Goi tests dat module pyodbc gia (tests/fake_pyodbc) vao sys.modules truoc khi nap mdf2sql, va tro
LOCALAPPDATA, PUBLIC vao thu muc tam: server.json, thu muc lam viec cua bai thu khong dung toi may that.
Thu muc tam: bien TRAMTOOL_TEST_ROOT (Tram Tool dat) hoac mot thu muc tempfile.
"""

import atexit
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_DIR = os.path.join(HERE, "fake_pyodbc")
REPO = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location("pyodbc", os.path.join(FAKE_DIR, "pyodbc.py"))
fake_pyodbc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_pyodbc)
sys.modules["pyodbc"] = fake_pyodbc

if os.environ.get("TRAMTOOL_TEST_ROOT"):
    TMP = os.path.join(os.environ["TRAMTOOL_TEST_ROOT"], "mdf2sql")
    os.makedirs(TMP, exist_ok=True)
else:
    TMP = tempfile.mkdtemp(prefix="mdf2sql_test_")
    atexit.register(shutil.rmtree, TMP, True)

os.environ["LOCALAPPDATA"] = os.path.join(TMP, "localappdata")
os.environ["PUBLIC"] = os.path.join(TMP, "public")
os.makedirs(os.environ["LOCALAPPDATA"], exist_ok=True)
os.makedirs(os.environ["PUBLIC"], exist_ok=True)
