"""Do tim SQL Server tren may va mo ket noi ODBC."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

try:
    import pyodbc
except ImportError:  # pragma: no cover - bao loi ro rang cho nguoi dung
    pyodbc = None

# Uu tien driver moi nhat -> cu nhat.
DRIVER_PREFERENCE = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13.1 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "ODBC Driver 11 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
]


class Mdf2SqlError(RuntimeError):
    """Loi nghiep vu, thong diep da o dang nguoi dung doc duoc."""


@dataclass
class Instance:
    """Mot instance SQL Server tim thay tren may."""

    server: str          # chuoi dung de ket noi, vd ".\SQLEXPRESS" hay "(localdb)\MSSQLLocalDB"
    label: str           # ten hien thi
    kind: str            # "service" | "localdb"
    running: bool = True

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.label} [{self.server}]"


def pick_driver() -> str:
    if pyodbc is None:
        raise Mdf2SqlError(
            "Chưa có thư viện pyodbc. Chạy lệnh:  python -m pip install --user pyodbc"
        )
    available = set(pyodbc.drivers())
    for drv in DRIVER_PREFERENCE:
        if drv in available:
            return drv
    raise Mdf2SqlError(
        "Không tìm thấy ODBC driver cho SQL Server. Hãy cài 'ODBC Driver 18 for SQL Server'.\n"
        "  winget install Microsoft.msodbcsql.18"
    )


def _ps(cmd: str, timeout: int = 60) -> str:
    """Chay 1 cau lenh PowerShell, tra ve stdout (rong neu loi)."""
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return out.stdout or ""
    except Exception:
        return ""


def find_localdb_exe() -> str:
    """Tim SqlLocalDB.exe. Trinh cai khong luon cap nhat PATH cua tien trinh dang chay."""
    exe = shutil.which("sqllocaldb")
    if exe:
        return exe
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramW6432", r"C:\Program Files"),
    ]
    for root in dict.fromkeys(roots):
        for ver in ("170", "160", "150", "140", "130", "120", "110"):
            candidate = os.path.join(root, "Microsoft SQL Server", ver,
                                     "Tools", "Binn", "SqlLocalDB.exe")
            if os.path.isfile(candidate):
                return candidate
    return ""


def discover_instances() -> list[Instance]:
    """Liet ke cac instance SQL Server: service thuong + LocalDB."""
    found: list[Instance] = []

    # 1. Instance dang chay duoi dang Windows service.
    out = _ps(
        "Get-Service -Name 'MSSQL*' -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.Name + '|' + $_.Status }"
    )
    for line in out.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        name, status = line.split("|", 1)
        running = status.strip().lower() == "running"
        if name == "MSSQLSERVER":
            found.append(Instance(".", "SQL Server (default instance)", "service", running))
        elif name.startswith("MSSQL$"):
            inst = name.split("$", 1)[1]
            found.append(Instance(f".\\{inst}", f"SQL Server ({inst})", "service", running))

    # 2. LocalDB - chay user-mode, khong hien ra nhu mot service.
    exe = find_localdb_exe()
    if exe:
        try:
            out = subprocess.run([exe, "info"], capture_output=True, text=True,
                                 timeout=60).stdout or ""
        except Exception:
            out = ""
        for line in out.splitlines():
            inst = line.strip()
            if not inst or inst.lower().startswith("sqllocaldb"):
                continue
            found.append(Instance(f"(localdb)\\{inst}",
                                  f"SQL Server LocalDB ({inst})", "localdb", True))

    return found


def ensure_localdb_started(instance: str = "MSSQLLocalDB") -> bool:
    """Khoi dong LocalDB neu dang tat. LocalDB tu tat sau mot luc khong dung."""
    exe = find_localdb_exe()
    if not exe:
        return False
    try:
        subprocess.run([exe, "start", instance], capture_output=True, text=True, timeout=180)
        return True
    except Exception:
        return False


def connect(server: str, database: str = "master", timeout: int = 30,
            autocommit: bool = True) -> "pyodbc.Connection":
    """Mo ket noi Windows Authentication toi instance."""
    driver = pick_driver()
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
        "Trusted_Connection=yes",
    ]
    # Driver 18 bat ma hoa mac dinh -> ket noi local se fail vi cert self-signed.
    if driver.startswith("ODBC Driver 18"):
        parts += ["Encrypt=no", "TrustServerCertificate=yes"]
    elif driver.startswith("ODBC Driver 17"):
        parts += ["TrustServerCertificate=yes"]
    conn_str = ";".join(parts) + ";"
    try:
        conn = pyodbc.connect(conn_str, timeout=timeout, autocommit=autocommit)
    except pyodbc.Error as exc:
        raise Mdf2SqlError(f"Không kết nối được SQL Server '{server}': {_clean(exc)}") from exc

    # DBCC tra ve cot kieu sql_variant ma pyodbc chua hieu -> tu giai ma sang chuoi.
    for sqltype in (-15, -150, -155):
        conn.add_output_converter(sqltype, _variant_to_text)
    return conn


def _variant_to_text(raw):
    """Giai ma gia tri sql_variant tho thanh chuoi doc duoc."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    try:
        text = bytes(raw).decode("utf-16-le").rstrip("\x00").strip()
        if text and all(ch.isprintable() or ch.isspace() for ch in text):
            return text
    except Exception:
        pass
    try:
        text = bytes(raw).decode("latin-1").rstrip("\x00").strip()
        return text
    except Exception:
        return repr(raw)


def server_info(conn) -> dict:
    row = conn.cursor().execute(
        "SELECT SERVERPROPERTY('ProductVersion'), SERVERPROPERTY('Edition'),"
        " SERVERPROPERTY('InstanceDefaultDataPath'), @@VERSION,"
        " CAST(SERVERPROPERTY('IsIntegratedSecurityOnly') AS int)"
    ).fetchone()
    version = str(row[0] or "")
    major = int(version.split(".")[0]) if version.split(".")[0].isdigit() else 0
    return {
        "version": version,
        "major": major,
        "edition": str(row[1] or ""),
        "default_data_path": str(row[2] or ""),
        "banner": str(row[3] or "").splitlines()[0].strip(),
    }


def _clean(exc: Exception) -> str:
    """Rut gon message ODBC dai dong thanh cau ngan."""
    msg = str(exc)
    msg = re.sub(r"\[[^\]]*\]", "", msg)
    msg = msg.replace("(0)", "").replace("(None)", "")
    msg = re.sub(r"\s+", " ", msg).strip(" ;()'\"")
    return msg[:400] or exc.__class__.__name__


def _trim_field(raw: str) -> str:
    """Cat ten database ra khoi vung dem cua boot page.

    Vung dem la cac byte 0x20 0x20 lien tiep; doc theo UTF-16 thi ra ky tu U+2020
    chu khong phai khoang trang, nen strip() thong thuong khong cat duoc.
    """
    chars = []
    for ch in raw:
        if ch in ("\x00", "†") or (ch < " " and ch not in "\t"):
            break
        chars.append(ch)
    return "".join(chars).strip()


def mdf_boot_info(mdf_path: str) -> dict:
    """Doc boot page (page 9) cua .mdf de lay ten DB + phien ban, KHONG can SQL Server."""
    # Ban do internal version -> ten phien ban SQL Server.
    versions = {
        539: "SQL Server 2000", 611: "SQL Server 2005", 612: "SQL Server 2005",
        655: "SQL Server 2008", 661: "SQL Server 2008 R2", 662: "SQL Server 2012",
        706: "SQL Server 2014", 782: "SQL Server 2016 CTP", 852: "SQL Server 2016",
        869: "SQL Server 2017", 895: "SQL Server 2019 CTP", 904: "SQL Server 2019",
        915: "SQL Server 2022 CTP", 957: "SQL Server 2022", 967: "SQL Server 2025",
    }
    info = {"db_name": "", "internal_version": 0, "created_version": 0,
            "server_version": "", "created_by": "", "page_count": 0}
    size = os.path.getsize(mdf_path)
    info["page_count"] = size // 8192
    with open(mdf_path, "rb") as fh:
        fh.seek(9 * 8192)
        page = fh.read(8192)
    if len(page) < 8192 or page[1] != 13:      # m_type == 13 -> boot page
        return info
    rec = page[0x60:]                          # record duy nhat cua boot page
    info["internal_version"] = int.from_bytes(rec[0x04:0x06], "little")
    info["created_version"] = int.from_bytes(rec[0x06:0x08], "little")
    raw = page[0x94:0x94 + 256].decode("utf-16-le", errors="replace")
    info["db_name"] = _trim_field(raw)
    info["server_version"] = versions.get(info["internal_version"], "")
    info["created_by"] = versions.get(info["created_version"], "")
    return info
