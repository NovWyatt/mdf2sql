"""Kiem tra moi truong chay mdf2sql (pyodbc, driver ODBC, SQL Server, thu muc lam viec, cong)
va in cach sua bang tieng Viet. Chi doc, khong cai hay sua gi.

Ma thoat: 0 du dieu kien, 2 thieu dieu kien (co muc [X]), 1 loi bat ngo.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.request

from . import __version__, attacher, dbconn, server

MODERN = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")
TRAM_ODBC = "nút 'Cài ODBC 18' ở trang công cụ trong Trạm Tool, hoặc: winget install Microsoft.msodbcsql.18"
TRAM_LOCALDB = "nút 'Cài LocalDB' ở trang công cụ trong Trạm Tool (cần quyền Admin), hoặc cài SQL Server Express"


class Report:
    def __init__(self, quiet: bool = False):
        self.items: list[dict] = []
        self.quiet = quiet

    def add(self, level: str, title: str, detail: str = "", fix: str = "", code: str = "") -> None:
        """level: ok | info | warn | bad (bad = thieu dieu kien, chuyen doi se khong chay)."""
        self.items.append({"ma": code, "muc": level, "tieu_de": title, "chi_tiet": detail, "cach_sua": fix})
        if self.quiet:
            return
        tag = {"ok": "[OK]", "info": "[i] ", "warn": "[!] ", "bad": "[X] "}[level]
        print(f"  {tag} {title}", flush=True)
        if detail:
            for line in detail.splitlines():
                print("       " + line, flush=True)
        if fix:
            print("       Cách sửa: " + fix, flush=True)

    @property
    def bad(self) -> int:
        return sum(1 for i in self.items if i["muc"] == "bad")


def _registry_drivers() -> list[str]:
    """Driver ODBC da cai, doc tu registry (dung khi chua co pyodbc)."""
    try:
        import winreg
        names = []
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers") as key:
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                if str(val).lower() == "installed":
                    names.append(name)
                i += 1
        return names
    except (ImportError, OSError):
        return []


def _check_python(rep: Report) -> None:
    v = sys.version_info
    text = f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if v < (3, 9):
        rep.add("bad", text, fix="Cài Python 3.12 (nút Cài trong Trạm Tool hoặc python.org).", code="PYTHON_CU")
    else:
        rep.add("ok", text, code="PYTHON")


def _check_pyodbc(rep: Report) -> bool:
    if dbconn.pyodbc is None:
        rep.add("bad", "Chưa có thư viện pyodbc",
                fix="python -m pip install --user pyodbc  (nút Chuyển đổi trong Trạm Tool cũng tự cài lần đầu)",
                code="THIEU_PYODBC")
        return False
    rep.add("ok", "pyodbc " + str(getattr(dbconn.pyodbc, "version", "?")), code="PYODBC")
    return True


def _check_driver(rep: Report, have_pyodbc: bool) -> str:
    """Tra ve driver se dung ('' khi khong co driver nao dung duoc)."""
    names = list(dbconn.pyodbc.drivers()) if have_pyodbc else _registry_drivers()
    sql = [d for d in dbconn.DRIVER_PREFERENCE if d in names]
    if not sql:
        rep.add("bad", "Chưa có driver ODBC cho SQL Server", fix=TRAM_ODBC, code="THIEU_ODBC")
        return ""
    best = sql[0]
    others = ", ".join(sql[1:])
    if best in MODERN:
        rep.add("ok", "Driver ODBC: " + best, detail=("Driver khác: " + others) if others else "", code="ODBC")
    elif best == "SQL Server":
        rep.add("bad", "Chỉ có driver ODBC 'SQL Server' rất cũ (có sẵn trong Windows)",
                detail="Driver này không kết nối được LocalDB và SQL Server đời mới.", fix=TRAM_ODBC, code="ODBC_QUA_CU")
        return ""
    else:
        rep.add("warn", "Driver ODBC cũ: " + best, detail="Vẫn chạy được với SQL Server Express; LocalDB có thể không kết nối.",
                fix="Nên cài ODBC Driver 18: " + TRAM_ODBC, code="ODBC_CU")
    return best


def _check_instances(rep: Report, forced: str) -> str:
    """Tra ve instance se dung (giong convert: --server hoac instance dang chay dau tien)."""
    found = dbconn.discover_instances()
    lines = [f"{i.label} -> {i.server} ({'đang chạy' if i.running else 'đang tắt'})" for i in found]
    if forced:
        rep.add("info", "Dùng instance chỉ định: " + forced, detail="\n".join(lines), code="SQL_CHI_DINH")
        return forced
    running = [i for i in found if i.running]
    if not found:
        rep.add("bad", "Chưa có SQL Server trên máy", fix=TRAM_LOCALDB, code="THIEU_SQL")
        return ""
    if not running:
        rep.add("bad", "Có SQL Server nhưng đang tắt", detail="\n".join(lines),
                fix="Bật dịch vụ MSSQL$<tên> trong services.msc (kiểu khởi động Automatic), hoặc " + TRAM_LOCALDB,
                code="SQL_TAT")
        return ""
    rep.add("ok", f"SQL Server: {len(found)} instance, sẽ dùng {running[0].server}", detail="\n".join(lines), code="SQL")
    return running[0].server


def _check_connect(rep: Report, srv: str) -> None:
    """Ket noi that toi instance (LocalDB tu khoi dong khi ket noi), chi chay cau SELECT."""
    try:
        conn = dbconn.connect(srv, timeout=15)
    except dbconn.Mdf2SqlError as exc:
        fix = ("Chạy: sqllocaldb start MSSQLLocalDB, rồi thử lại" if srv.lower().startswith("(localdb)")
               else "Kiểm tra dịch vụ SQL Server đang chạy và tài khoản Windows này đăng nhập được SQL Server")
        rep.add("bad", "Không kết nối được " + srv, detail=str(exc), fix=fix, code="KHONG_KET_NOI")
        return
    try:
        info = dbconn.server_info(conn)
        rep.add("ok", f"Kết nối được {srv}: {info['banner']}", detail="Phiên bản " + info["version"] + ", " + info["edition"],
                code="KET_NOI")
        cur = conn.cursor()
        row = cur.execute("SELECT IS_SRVROLEMEMBER('sysadmin'), IS_SRVROLEMEMBER('dbcreator')").fetchone()
        if not (row[0] == 1 or row[1] == 1):
            rep.add("bad", "Tài khoản Windows này không được tạo, gắn database trên " + srv,
                    fix="Dùng LocalDB (tài khoản của bạn luôn là chủ), hoặc nhờ người quản trị SQL thêm vào dbcreator",
                    code="THIEU_QUYEN_SQL")
        temp = [r[0] for r in cur.execute(
            "SELECT name FROM sys.databases WHERE name LIKE '%\\_\\_m2s\\_%' ESCAPE '\\'").fetchall()]
        if temp:
            rep.add("warn", f"{len(temp)} database tạm của lần chạy trước còn gắn: " + ", ".join(temp[:5]),
                    detail="Thường do đóng cửa sổ khi đang chuyển đổi.",
                    fix="Khi không có cửa sổ mdf2sql nào đang chuyển: SSMS > chuột phải database > Tasks > Detach",
                    code="DB_TAM_SOT")
    except Exception as exc:              # truy van thu loi khong lam hong ket qua kiem tra ket noi
        rep.add("warn", "Không đọc được thông tin máy chủ: " + dbconn._clean(exc), code="KHONG_DOC_SERVER")
    finally:
        conn.close()


def _check_workspace(rep: Report) -> None:
    ws = attacher.default_workspace()
    parent = os.path.dirname(ws)
    if not os.path.isdir(parent):
        rep.add("bad", "Không có thư mục " + parent, fix="Đặt biến môi trường PUBLIC trỏ tới thư mục ghi được", code="THIEU_THU_MUC")
        return
    left = []
    size = 0
    if os.path.isdir(ws):
        for name in os.listdir(ws):
            p = os.path.join(ws, name)
            if name.startswith("session_") and os.path.isdir(p):
                left.append(name)
                for root, _, files in os.walk(p):
                    for f in files:
                        try:
                            size += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
    if left:
        rep.add("warn", f"{len(left)} thư mục làm việc của lần chạy trước còn sót ({size / 1048576:.1f} MB) trong {ws}",
                detail="Chứa bản sao .mdf của khách.",
                fix="Xoá khi không có cửa sổ mdf2sql nào đang chuyển (gỡ database tạm trước nếu có)", code="THU_MUC_SOT")
    else:
        rep.add("ok", "Thư mục làm việc: " + ws, code="THU_MUC")


def _check_port(rep: Report) -> None:
    try:
        with open(server.state_path(), encoding="utf-8") as fh:
            st = json.load(fh)
        with urllib.request.urlopen(f"http://127.0.0.1:{int(st['port'])}/api/health", timeout=2) as r:
            h = json.loads(r.read().decode("utf-8"))
        if h.get("app") == "mdf2sql" and h.get("pid") == st.get("pid"):
            rep.add("info", f"Giao diện mdf2sql đang mở ở cổng {h['port']} (tiến trình {h['pid']}, "
                            f"{h.get('jobs_running', 0)} việc đang chạy)", code="DANG_CHAY")
            return
    except Exception:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", server.DEFAULT_PORT))
        rep.add("ok", f"Cổng {server.DEFAULT_PORT} trống", code="CONG")
    except OSError:
        rep.add("info", f"Cổng {server.DEFAULT_PORT} đang bận: giao diện sẽ tự dùng cổng khác", code="CONG_BAN")
    finally:
        s.close()


def run(srv: str = "", connect: bool = True, as_json: bool = False) -> int:
    rep = Report(quiet=as_json)
    if not as_json:
        print(f"mdf2sql {__version__}: kiểm tra môi trường", flush=True)
    _check_python(rep)
    have = _check_pyodbc(rep)
    drv = _check_driver(rep, have)
    target = _check_instances(rep, srv)
    if connect and have and drv and target:
        _check_connect(rep, target)
    elif connect and target:
        rep.add("info", "Chưa thử kết nối SQL Server (thiếu pyodbc hoặc driver ODBC)", code="CHUA_THU_KET_NOI")
    _check_workspace(rep)
    _check_port(rep)
    code = 2 if rep.bad else 0
    if as_json:
        print(json.dumps({"cong_cu": "mdf2sql", "phien_ban": __version__, "ma_thoat": code,
                          "thieu": rep.bad, "muc": rep.items}, ensure_ascii=False, indent=2), flush=True)
    elif rep.bad:
        print(f"\nThiếu {rep.bad} điều kiện: sửa theo hướng dẫn ở trên rồi chạy lại 'mdf2sql doctor'.", flush=True)
    else:
        print("\nĐủ điều kiện để chuyển đổi.", flush=True)
    return code
