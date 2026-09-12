"""Copy file .mdf ra vung lam viec an toan roi attach vao SQL Server.

Nguyen tac: KHONG BAO GIO dung toi file goc cua khach.
Attach khien SQL Server ghi de va nang cap header file, nen luon lam viec tren ban sao.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field

from .dbconn import Mdf2SqlError, _clean


@dataclass
class AttachResult:
    db_name: str
    strategy: str = ""                  # cach attach da thanh cong
    repaired: bool = False              # co phai chay DBCC REPAIR khong
    warnings: list = field(default_factory=list)   # dieu nguoi dung can biet
    details: list = field(default_factory=list)    # ky thuat, chi hien khi can tra cuu
    work_files: list = field(default_factory=list)


def q(name) -> str:
    """Boc ten doi tuong trong dau ngoac vuong, escape ] thanh ]]."""
    return "[" + str(name).replace("]", "]]") + "]"


def lit(text) -> str:
    """Boc chuoi thanh literal T-SQL Unicode."""
    return "N'" + str(text).replace("'", "''") + "'"


def safe_db_name(raw: str) -> str:
    """Tao ten database tam hop le tu ten goc."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw or "mdf")[:60].strip("_") or "mdf"
    if name[0].isdigit():
        name = "db_" + name
    return name + "__m2s_" + uuid.uuid4().hex[:6]


def default_workspace() -> str:
    """Thu muc lam viec mac dinh, moi user deu ghi duoc."""
    base = os.environ.get("PUBLIC") or os.path.expanduser("~")
    return os.path.join(base, "mdf2sql_work")


def grant_access(path: str, instance_kind: str, server: str) -> list:
    """Cap quyen doc/ghi cho tai khoan dich vu SQL Server tren thu muc lam viec.

    LocalDB chay bang chinh tai khoan nguoi dung nen khong can cap gi them.
    """
    notes = []
    if instance_kind == "localdb":
        return notes
    accounts = []
    inst = server.lstrip(".").lstrip("\\")
    if inst and inst.lower() not in ("", "localhost", "(local)"):
        accounts.append("NT SERVICE\\MSSQL$" + inst)
    accounts.append("NT SERVICE\\MSSQLSERVER")
    for acct in accounts:
        try:
            r = subprocess.run(
                ["icacls", path, "/grant", acct + ":(OI)(CI)F", "/T", "/Q"],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                notes.append("Đã cấp quyền thư mục làm việc cho " + acct)
                return notes
        except Exception:
            continue
    notes.append(
        "Không cấp được quyền thư mục cho tài khoản SQL Server. Nếu báo lỗi "
        "'Access is denied', hãy chạy tool bằng quyền Administrator."
    )
    return notes


def probe_primary_file(conn, mdf_path: str) -> dict:
    """Dung DBCC CHECKPRIMARYFILE doc metadata trong .mdf ma khong can attach."""
    info = {"db_name": "", "files": []}
    cur = conn.cursor()
    try:
        cur.execute("DBCC CHECKPRIMARYFILE (" + lit(mdf_path) + ", 2) WITH NO_INFOMSGS")
        for row in cur.fetchall():
            prop = str(row[0]).strip().lower()
            val = str(row[1]).strip() if row[1] is not None else ""
            if prop == "database name":
                info["db_name"] = val
            elif prop in ("database version", "collation"):
                info[prop.replace(" ", "_")] = val
    except Exception as exc:
        info["probe_error"] = _clean(exc)
    try:
        cur.execute("DBCC CHECKPRIMARYFILE (" + lit(mdf_path) + ", 3) WITH NO_INFOMSGS")
        cols = [d[0].lower() for d in cur.description]
        for row in cur.fetchall():
            vals = [str(v).strip() if v is not None else "" for v in row]
            info["files"].append(dict(zip(cols, vals)))
    except Exception:
        pass
    return info


def stage_files(mdf_path: str, workspace: str):
    """Copy .mdf (kem .ldf/.ndf cung ten) sang thu muc lam viec.

    Tra ve (duong dan mdf moi, danh sach tat ca file da copy).
    """
    mdf_path = os.path.abspath(mdf_path)
    if not os.path.isfile(mdf_path):
        raise Mdf2SqlError("Không tìm thấy file: " + mdf_path)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest_dir = os.path.join(workspace, "session_" + stamp + "_" + uuid.uuid4().hex[:4])
    os.makedirs(dest_dir, exist_ok=True)

    src_dir = os.path.dirname(mdf_path) or "."
    stem = os.path.splitext(os.path.basename(mdf_path))[0].lower()

    copied = []
    new_mdf = os.path.join(dest_dir, os.path.basename(mdf_path))
    shutil.copy2(mdf_path, new_mdf)
    copied.append(new_mdf)

    # Lay kem file log va file du lieu phu neu trung ten goc.
    for entry in sorted(os.listdir(src_dir)):
        low = entry.lower()
        if not low.endswith((".ldf", ".ndf")):
            continue
        base = os.path.splitext(low)[0]
        if not (base == stem or base.startswith(stem + "_") or base.startswith(stem + ".")):
            continue
        dst = os.path.join(dest_dir, entry)
        shutil.copy2(os.path.join(src_dir, entry), dst)
        copied.append(dst)

    return new_mdf, copied


def _exec(conn, sql: str, timeout: int = 0) -> None:
    """Chay 1 cau lenh, vet het cac result set phu (DBCC hay tra nhieu set)."""
    previous = getattr(conn, "timeout", 0)
    try:
        conn.timeout = timeout          # pyodbc: timeout nam o connection, khong phai cursor
    except Exception:
        pass
    try:
        cur = conn.cursor()
        cur.execute(sql)
        while cur.nextset():
            pass
    finally:
        try:
            conn.timeout = previous
        except Exception:
            pass


def attach(conn, mdf_path: str, files: list, db_name: str, progress=None,
           logical=None) -> AttachResult:
    """Attach .mdf vao SQL Server, thu lan luot tu cach nhe den cach manh.

    logical: danh sach dict tu probe_primary_file()['files'] - dung de dat dung
    ten logical goc khi phai cuu ho, giup SQL Server nhan file de hon.
    """
    if progress is None:
        def progress(_m):
            return None

    data_files = [f for f in files if f.lower().endswith((".mdf", ".ndf"))]
    log_files = [f for f in files if f.lower().endswith(".ldf")]
    res = AttachResult(db_name=db_name, work_files=list(files))

    # Cach 1: attach day du ca file log - chinh xac nhat khi con .ldf.
    if log_files:
        spec = ", ".join("(FILENAME = " + lit(f) + ")" for f in data_files + log_files)
        try:
            progress("Đang gắn database kèm file log (.ldf)...")
            _exec(conn, "CREATE DATABASE " + q(db_name) + " ON " + spec + " FOR ATTACH", 600)
            res.strategy = "Gắn trực tiếp kèm file log"
            return res
        except Exception as exc:
            res.details.append("Gắn kèm log thất bại: " + _clean(exc))

    # Cach 2: khong co log -> yeu cau SQL Server dung lai log moi.
    spec = ", ".join("(FILENAME = " + lit(f) + ")" for f in data_files)
    try:
        progress("Đang gắn database và dựng lại file log...")
        _exec(conn, "CREATE DATABASE " + q(db_name) + " ON " + spec +
              " FOR ATTACH_REBUILD_LOG", 900)
        res.strategy = "Gắn database và tự dựng lại file log"
        res.warnings.append(
            "Không có file log hợp lệ nên SQL Server đã tạo log mới. Giao dịch còn dang "
            "dở lúc hệ thống lỗi sẽ không được khôi phục."
        )
        return res
    except Exception as exc:
        res.details.append("Dựng lại log khi gắn thất bại: " + _clean(exc))

    # Cach 3: hack-attach - cuu ho file hong hoac tat dot ngot (dirty shutdown).
    progress("File tắt đột ngột, chuyển sang chế độ cứu hộ...")
    _hack_attach(conn, data_files, db_name, res, progress, logical)
    return res


def _logical_names(logical, count: int):
    """Tach ten logical goc thanh (danh sach ten file du lieu, ten file log)."""
    data_names, log_name = [], None
    for rec in logical or []:
        name = rec.get("name") or ""
        if not name:
            continue
        # status bit 0x40 (1048576 + ...) danh dau file log; du phong: ten ket thuc _log
        is_log = False
        try:
            is_log = bool(int(rec.get("status", "0")) & 0x40)
        except (TypeError, ValueError):
            is_log = name.lower().endswith("_log")
        if is_log:
            log_name = log_name or name
        else:
            data_names.append(name)
    while len(data_names) < count:
        data_names.append("f" + str(len(data_names)))
    return data_names[:count], (log_name or "f_log")


def _hack_attach(conn, data_files: list, db_name: str, res: AttachResult,
                 progress, logical=None) -> None:
    """Tao DB rong cung so file, trao file, vao EMERGENCY roi DBCC CHECKDB sua loi."""
    tmp_dir = os.path.dirname(data_files[0])
    placeholder = [os.path.join(tmp_dir, "_ph_" + str(i) + "_" + os.path.basename(f))
                   for i, f in enumerate(data_files)]
    ph_log = os.path.join(tmp_dir, "_ph_log.ldf")

    data_names, log_name = _logical_names(logical, len(data_files))
    parts = ["(NAME = " + lit(data_names[i]) + ", FILENAME = " + lit(p) + ", SIZE = 8MB)"
             for i, p in enumerate(placeholder)]
    try:
        _exec(conn, "CREATE DATABASE " + q(db_name) + " ON " + ", ".join(parts) +
              " LOG ON (NAME = " + lit(log_name) + ", FILENAME = " + lit(ph_log) +
              ", SIZE = 8MB)", 300)
    except Exception as exc:
        raise Mdf2SqlError(
            "Không tạo được database tạm để cứu hộ file. Chi tiết: " + _clean(exc)) from exc

    try:
        # Dat SINGLE_USER NGAY BAY GIO, luc DB tam con khoe. Sau khi trao file, DB cu
        # hon phien ban server se khong cho ALTER nua (loi 946), nhung thiet lap nay
        # da nam trong metadata cua master nen van con hieu luc.
        progress("Khóa database tạm ở chế độ một người dùng...")
        _exec(conn, "ALTER DATABASE " + q(db_name) +
              " SET SINGLE_USER WITH ROLLBACK IMMEDIATE", 300)

        progress("Đưa database tạm offline để tráo file...")
        _exec(conn, "ALTER DATABASE " + q(db_name) + " SET OFFLINE WITH ROLLBACK IMMEDIATE", 300)
        for src, dst in zip(data_files, placeholder):
            os.replace(src, dst)
        try:
            os.remove(ph_log)
        except OSError:
            pass

        progress("Đưa database online, dự kiến sẽ ở trạng thái chờ phục hồi...")
        try:
            _exec(conn, "ALTER DATABASE " + q(db_name) + " SET ONLINE", 600)
        except Exception:
            pass  # RECOVERY_PENDING / SUSPECT la ket qua mong doi o buoc nay

        progress("Bật chế độ khẩn cấp...")
        _exec(conn, "ALTER DATABASE " + q(db_name) + " SET EMERGENCY", 300)

        # Dung lai file log tu dau. Day la cach duy nhat lam viec duoc khi file .mdf
        # cu hon phien ban SQL Server dang chay (vd .mdf 2014 tren SQL Server 2022):
        # DBCC CHECKDB se bi tu choi voi loi 946, con REBUILD LOG thi khong.
        new_log = os.path.join(tmp_dir, "rebuilt_log.ldf")
        progress("Dựng lại file log từ đầu...")
        try:
            _exec(conn, "ALTER DATABASE " + q(db_name) + " REBUILD LOG ON (NAME = " +
                  lit(log_name) + ", FILENAME = " + lit(new_log) + ")", 0)
            res.strategy = "Cứu hộ: tráo file, chế độ khẩn cấp, dựng lại log"
        except Exception as rebuild_exc:
            res.details.append("Dựng lại log thất bại: " + _clean(rebuild_exc))
            progress("Chuyển sang sửa chữa sâu, bước này có thể lâu...")
            _exec(conn, "DBCC CHECKDB (" + q(db_name) + ", REPAIR_ALLOW_DATA_LOSS) "
                  "WITH NO_INFOMSGS, ALL_ERRORMSGS", 0)
            res.strategy = "Cứu hộ: tráo file, chế độ khẩn cấp, sửa chữa sâu"
            res.warnings.append(
                "Đã phải sửa file ở mức cho phép mất dữ liệu. Một số trang hỏng có thể "
                "đã bị bỏ. Hãy đối chiếu lại số liệu quan trọng như vé xe và doanh thu."
            )

        for sql in ("SET MULTI_USER", "SET ONLINE"):
            try:
                _exec(conn, "ALTER DATABASE " + q(db_name) + " " + sql, 600)
            except Exception:
                pass

        res.repaired = True
        res.work_files = placeholder
        res.warnings.append(
            "File .mdf bị tắt đột ngột và không có file log hợp lệ. Log đã được dựng "
            "lại từ đầu, nên giao dịch chưa ghi xong lúc hệ thống lỗi sẽ không còn. "
            "Dữ liệu đã ghi xong đều được giữ nguyên."
        )
    except Exception as exc:
        try:
            _exec(conn, "ALTER DATABASE " + q(db_name) + " SET OFFLINE WITH ROLLBACK IMMEDIATE")
            _exec(conn, "DROP DATABASE " + q(db_name))
        except Exception:
            pass
        raise Mdf2SqlError("Không cứu hộ được file .mdf này. Chi tiết: " + _clean(exc)) from exc


def repair(conn, db_name: str, progress=None) -> dict:
    """Chay DBCC CHECKDB REPAIR_ALLOW_DATA_LOSS de go cac loi cau truc con lai.

    Buoc nay chi lam duoc SAU khi database da online (da qua REBUILD LOG), luc do
    file da duoc nang len dung phien ban cua SQL Server dang chay.
    """
    if progress is None:
        def progress(_m):
            return None
    out = {"ran": False, "messages": []}
    progress("Đang sửa lỗi cấu trúc, bước này có thể lâu...")
    previous = getattr(conn, "timeout", 0)
    try:
        conn.timeout = 0
        _exec(conn, "ALTER DATABASE " + q(db_name) + " SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
        try:
            cur = conn.cursor()
            cur.execute("DBCC CHECKDB (" + q(db_name) + ", REPAIR_ALLOW_DATA_LOSS) "
                        "WITH NO_INFOMSGS, ALL_ERRORMSGS, TABLERESULTS")
            while True:
                try:
                    cur.fetchall()
                except Exception:
                    pass
                if not cur.nextset():
                    break
        except Exception as exc:
            # DBCC bao loi muc 16 ngay ca khi da sua duoc -> ghi nhan, khong dung lai.
            out["messages"].append(_clean(exc))
        out["ran"] = True
    except Exception as exc:
        out["messages"].append("Không chạy được bước sửa chữa: " + _clean(exc))
    finally:
        try:
            _exec(conn, "ALTER DATABASE " + q(db_name) + " SET MULTI_USER")
        except Exception:
            pass
        try:
            conn.timeout = previous
        except Exception:
            pass
    return out


def check_integrity(conn, db_name: str, progress=None) -> dict:
    """Chay DBCC CHECKDB che do chi doc de bao cao muc do toan ven cua du lieu."""
    if progress is None:
        def progress(_m):
            return None
    progress("Kiểm tra toàn vẹn dữ liệu...")
    report = {"ok": False, "errors": 0, "messages": []}
    previous = getattr(conn, "timeout", 0)
    try:
        conn.timeout = 0
        cur = conn.cursor()
        cur.execute("DBCC CHECKDB (" + q(db_name) + ") WITH NO_INFOMSGS, ALL_ERRORMSGS, TABLERESULTS")
        if cur.description is None:
            # Khong co loi nao -> CHECKDB khong tra ve bang ket qua.
            report["ok"] = True
            return report
        rows = cur.fetchall()
        cols = [d[0].lower() for d in cur.description]
        idx_level = cols.index("level") if "level" in cols else None
        idx_text = cols.index("messagetext") if "messagetext" in cols else -1
        for row in rows:
            text = str(row[idx_text]) if idx_text >= 0 else str(row)
            level = int(row[idx_level]) if idx_level is not None and row[idx_level] else 0
            if level >= 16:
                report["errors"] += 1
                if len(report["messages"]) < 25:
                    report["messages"].append(text[:300])
        report["ok"] = report["errors"] == 0
    except Exception as exc:
        report["messages"].append("Không chạy được bước kiểm tra: " + _clean(exc))
    finally:
        try:
            conn.timeout = previous
        except Exception:
            pass
    return report


def detach(conn, db_name: str) -> None:
    """Go database khoi SQL Server, giu nguyen file tren dia."""
    try:
        _exec(conn, "ALTER DATABASE " + q(db_name) + " SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
    except Exception:
        pass
    try:
        _exec(conn, "EXEC master.dbo.sp_detach_db @dbname = " + lit(db_name) +
              ", @skipchecks = 'true'")
    except Exception:
        try:
            _exec(conn, "DROP DATABASE " + q(db_name))
        except Exception:
            pass


def cleanup(paths: list) -> None:
    """Xoa cac ban sao tam sau khi convert xong."""
    dirs = set()
    for p in paths:
        try:
            if os.path.isfile(p):
                os.remove(p)
            dirs.add(os.path.dirname(p))
        except OSError:
            pass
    for d in dirs:
        try:
            if d and os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass
