"""Dieu phoi toan bo quy trinh: .mdf  ->  attach  ->  doc schema  ->  file .sql."""

from __future__ import annotations

import datetime
import os
import time
from dataclasses import dataclass, field

from . import attacher, dbconn, introspect
from .emit import (MAX_ROWS_PER_INSERT, NL, ScriptWriter, column_ddl, find_duplicate_keys,
                   find_orphan_rows, insertable_columns, literal, q, qn)
from .introspect import is_binary_blob

# Kieu can doc duoi dang khac de khong mat do chinh xac.
_AS_TEXT = {"datetime2", "datetimeoffset", "time"}
_AS_BINARY_UDT = {"geometry", "geography", "hierarchyid"}


@dataclass
class Options:
    """Tuy chon xuat file .sql."""

    include_data: bool = True           # co xuat du lieu khong, hay chi cau truc
    create_database: bool = True        # co chen lenh CREATE DATABASE + USE khong
    target_db: str = ""                 # ten database dich, rong = giu ten goc
    drop_if_exists: bool = True         # xoa bang cu truoc khi tao, de chay lai duoc
    batch_rows: int = 500               # so dong moi cau INSERT
    skip_blobs: bool = False            # bo qua cot nhi phan lon (anh)
    auto_repair: bool = True            # tu dong sua loi cau truc neu CHECKDB bao loi
    exclude_tables: list = field(default_factory=list)   # "schema.table" bo qua
    max_rows_per_table: int = 0         # 0 = xuat het


@dataclass
class Report:
    """Ket qua mot lan convert."""

    ok: bool = False
    out_path: str = ""
    db_name: str = ""
    strategy: str = ""
    repaired: bool = False
    tables: int = 0
    rows: int = 0
    rejected: int = 0                   # so dong bi loai vi du lieu rac
    rejected_path: str = ""             # file chua cac dong bi loai
    bytes_written: int = 0
    seconds: float = 0.0
    warnings: list = field(default_factory=list)   # dieu nguoi dung can biet
    details: list = field(default_factory=list)    # ky thuat, de trong muc thu gon
    integrity: dict = field(default_factory=dict)
    table_stats: list = field(default_factory=list)
    rejected_rows: list = field(default_factory=list)


def _noop(*_args, **_kwargs):
    return None


def _size_limit(col: dict):
    """Kich thuoc toi da cua cot, tinh theo don vi ma Python dem duoc (None = khong gioi han).

    Du lieu hop le luon nam trong gioi han nay. Gia tri vuot nguong chi xuat hien khi
    doc trung trang bi hong - do la dong rac, can tach ra de file chinh import sach.
    """
    tn = (col["type_name"] or "").lower()
    max_len = col["max_length"]
    if not max_len or max_len < 0:
        return None
    if tn in ("nchar", "nvarchar"):
        return int(max_len) // 2
    if tn in ("char", "varchar", "binary", "varbinary"):
        return int(max_len)
    return None


def _select_plan(cols: list, opts: Options):
    """Chon cach doc tung cot + cach viet lai thanh literal."""
    exprs, kinds, names, limits = [], [], [], []
    for c in cols:
        tn = (c["type_name"] or "").lower()
        name = c["name"]
        if opts.skip_blobs and is_binary_blob(c):
            continue
        limits.append(_size_limit(c))
        if tn in _AS_TEXT:
            exprs.append("CONVERT(varchar(34), " + q(name) + ", 126)")
            kinds.append("text")
        elif tn in _AS_BINARY_UDT:
            exprs.append("CAST(" + q(name) + " AS varbinary(max))")
            kinds.append("udt:" + tn)
        elif tn == "xml":
            exprs.append("CONVERT(nvarchar(max), " + q(name) + ")")
            kinds.append("nvarchar")
        elif tn == "sql_variant":
            exprs.append("CONVERT(nvarchar(max), " + q(name) + ")")
            kinds.append("nvarchar")
        else:
            exprs.append(q(name))
            kinds.append(tn)
        names.append(name)
    return exprs, kinds, names, limits


def _row_fits(row, limits) -> bool:
    """Dong co gia tri vuot kich thuoc cot -> du lieu rac tu trang hong."""
    for value, limit in zip(row, limits):
        if limit is None or value is None:
            continue
        if isinstance(value, str):
            if len(value) > limit:
                return False
        elif isinstance(value, (bytes, bytearray, memoryview)):
            if len(bytes(value)) > limit:
                return False
    return True


def _value_sql(value, kind: str) -> str:
    """Doi 1 o du lieu thanh doan SQL."""
    if value is None:
        return "NULL"
    if kind == "text":
        return "'" + str(value).replace("'", "''") + "'"
    if kind.startswith("udt:"):
        return "CONVERT(" + kind[4:] + ", 0x" + bytes(value).hex().upper() + ")"
    return literal(value, kind)


def generate_script(conn, model: dict, out_path: str, opts: Options,
                    progress=_noop) -> Report:
    """Sinh file .sql day du tu schema da doc va du lieu trong database."""
    started = time.time()
    rep = Report(out_path=out_path, db_name=model["database"].get("name") or "")
    db_collation = model["database"].get("collation_name")
    target_db = opts.target_db or rep.db_name

    excluded = {e.lower() for e in opts.exclude_tables}
    tables = [t for t in model["tables"]
              if (t["schema"] + "." + t["table"]).lower() not in excluded]
    rep.tables = len(tables)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    # UTF-8 co BOM: SSMS va sqlcmd moi doc dung tieng Viet co dau.
    # newline="": tu ghi '\r\n', khong de Python dong vao ky tu xuong dong cua du lieu.
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = ScriptWriter(fh)
        _write_header(w, model, opts, tables)

        if opts.create_database and target_db:
            w.section("1. Tạo database")
            w.write("IF DB_ID(N'" + target_db.replace("'", "''") + "') IS NULL")
            coll = (" COLLATE " + db_collation) if db_collation else ""
            w.write("    CREATE DATABASE " + q(target_db) + coll + ";")
            w.go()
            w.write("USE " + q(target_db) + ";")
            w.go()

        _write_schemas(w, model)
        _write_user_types(w, model)
        _write_tables(w, tables, db_collation, opts)

        if opts.include_data:
            _write_data(w, conn, tables, opts, rep, progress)
        else:
            rep.warnings.append("Đã chọn chỉ xuất cấu trúc, file không chứa dữ liệu.")

        _write_keys(w, conn, tables, rep, progress)
        _write_indexes(w, tables)
        _write_checks(w, model)
        _write_foreign_keys(w, conn, model, rep)
        _write_modules(w, model)
        _write_reseed(w, tables)

        w.section("Kết thúc")
        w.write("PRINT N'>>> Import hoàn tất.';")
        w.go()

    if rep.rejected_rows:
        rep.rejected_path = _write_rejected(out_path, rep)

    rep.bytes_written = os.path.getsize(out_path)
    rep.seconds = time.time() - started
    rep.ok = True
    return rep


def _write_rejected(out_path: str, rep: Report) -> str:
    """Ghi rieng cac dong bi loai ra 1 file de nguoi dung tu xem xet."""
    base, ext = os.path.splitext(out_path)
    path = base + "_dong-loi" + (ext or ".sql")
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = ScriptWriter(fh)
        w.write("/" + "*" * 72)
        w.write(" * Các dòng KHÔNG đưa vào file chính vì giá trị vượt kích thước cột.")
        w.write(" * Đây gần như chắc chắn là dữ liệu rác đọc từ trang đĩa bị hỏng.")
        w.write(" * Tổng số dòng bị loại: " + f"{rep.rejected:,}"
                + (" (file này lưu " + f"{len(rep.rejected_rows):,}" + " dòng đầu)"
                   if rep.rejected > len(rep.rejected_rows) else ""))
        w.write(" *")
        w.write(" * Muốn nạp lại thì phải sửa giá trị cho vừa cột, hoặc nới rộng cột trước.")
        w.write(" " + "*" * 71 + "/")
        current = None
        for item in rep.rejected_rows:
            if item["table"] != current:
                current = item["table"]
                w.write()
                w.write("-- " + current)
            w.write("-- INSERT INTO " + qn(*current.split(".", 1)) + " " +
                    item["columns"] + " VALUES " + item["values"] + ";")
    return path


def _write_header(w: ScriptWriter, model: dict, opts: Options, tables: list) -> None:
    db = model["database"]
    total_rows = sum(t["row_count"] for t in tables)
    w.write("/" + "*" * 72)
    w.write(" * Script sinh bởi mdf2sql, chuyển file .mdf sang .sql")
    w.write(" * Database gốc : " + str(db.get("name")))
    w.write(" * Collation    : " + str(db.get("collation_name")))
    w.write(" * Số bảng      : " + str(len(tables)))
    w.write(" * Số dòng (ước): " + f"{total_rows:,}")
    w.write(" * Thời điểm    : " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    w.write(" *")
    w.write(" * Cách chạy:")
    w.write(" *   - SSMS: mở file này rồi bấm Execute (F5)")
    w.write(" *   - Dòng lệnh: sqlcmd -S .\\SQLEXPRESS -E -f 65001 -i ten_file.sql")
    w.write(" *")
    w.write(" * Thứ tự trong file: tạo bảng, đổ dữ liệu, khoá chính, index,")
    w.write(" * check, khoá ngoại, rồi view và thủ tục. Nhờ vậy không vướng thứ tự")
    w.write(" * phụ thuộc giữa các bảng khi import.")
    w.write(" " + "*" * 71 + "/")
    w.write()
    w.write("SET NOCOUNT ON;")
    w.write("SET XACT_ABORT ON;")
    w.write("SET ANSI_NULLS ON;")
    w.write("SET QUOTED_IDENTIFIER ON;")
    w.go()


def _write_schemas(w: ScriptWriter, model: dict) -> None:
    if not model["schemas"]:
        return
    w.section("2. Schema")
    for name in model["schemas"]:
        w.write("IF SCHEMA_ID(N'" + name.replace("'", "''") + "') IS NULL")
        w.write("    EXEC(N'CREATE SCHEMA " + q(name).replace("'", "''") + "');")
        w.go()


def _write_user_types(w: ScriptWriter, model: dict) -> None:
    if not model["user_types"]:
        return
    w.section("3. Kiểu dữ liệu tự định nghĩa")
    for t in model["user_types"]:
        base = t["base_type"]
        if base in ("char", "varchar", "binary", "varbinary"):
            base += "(" + ("max" if t["max_length"] == -1 else str(t["max_length"])) + ")"
        elif base in ("nchar", "nvarchar"):
            base += "(" + ("max" if t["max_length"] == -1 else str(t["max_length"] // 2)) + ")"
        elif base in ("decimal", "numeric"):
            base += "(" + str(t["precision"]) + "," + str(t["scale"]) + ")"
        null = "NULL" if t["is_nullable"] else "NOT NULL"
        w.write("IF TYPE_ID(N'" + t["schema"] + "." + t["name"] + "') IS NULL")
        w.write("    CREATE TYPE " + qn(t["schema"], t["name"]) +
                " FROM " + base + " " + null + ";")
        w.go()


def _write_tables(w: ScriptWriter, tables: list, db_collation, opts: Options) -> None:
    w.section("4. Tạo bảng")
    for t in tables:
        full = qn(t["schema"], t["table"])
        if opts.drop_if_exists:
            w.write("IF OBJECT_ID(N'" + (t["schema"] + "." + t["table"]).replace("'", "''") +
                    "', N'U') IS NOT NULL DROP TABLE " + full + ";")
        cols = [c for c in t["columns"]
                if not (opts.skip_blobs and is_binary_blob(c))]
        lines = ["    " + column_ddl(c, db_collation) for c in cols]
        w.write("CREATE TABLE " + full + " (")
        w.write(("," + NL).join(lines))
        w.write(");")
        w.go()


def _clustered_key(table: dict) -> list:
    """Cot khoa cua chi muc clustered - dung de doc tiep sau khi gap trang hong."""
    for k in table["keys"]:
        if k["type_desc"] == "CLUSTERED":
            return [c for c, _ in k["columns"]]
    for i in table["indexes"]:
        if i["type_desc"] == "CLUSTERED":
            return [c for c, _ in i["key_columns"]]
    return []


def _keyset_where(key_cols: list) -> str:
    """Dieu kien 'lon hon bo khoa da doc' theo kieu keyset pagination.

    Vi du voi khoa (a, b):  (a > ?) OR (a = ? AND b > ?)
    """
    clauses = []
    for i in range(len(key_cols)):
        parts = [q(key_cols[j]) + " = ?" for j in range(i)]
        parts.append(q(key_cols[i]) + " > ?")
        clauses.append("(" + " AND ".join(parts) + ")")
    return " OR ".join(clauses)


def _keyset_args(values: list) -> list:
    """Danh sach tham so khop voi _keyset_where."""
    args = []
    for i in range(len(values)):
        args.extend(values[:i])
        args.append(values[i])
    return args


def _write_data(w: ScriptWriter, conn, tables: list, opts: Options,
                rep: Report, progress) -> None:
    w.section("5. Dữ liệu")
    batch_rows = max(1, min(opts.batch_rows, MAX_ROWS_PER_INSERT))
    done = 0
    for t in tables:
        done += 1
        progress("data", done, len(tables),
                 "Đang xuất " + t["schema"] + "." + t["table"] +
                 " (" + f"{t['row_count']:,}" + " dòng)")
        _write_one_table(w, conn, t, opts, rep, batch_rows)


def _write_one_table(w: ScriptWriter, conn, t: dict, opts: Options,
                     rep: Report, batch_rows: int) -> None:
    """Xuat du lieu 1 bang. Gap trang hong thi giu phan da doc va doc tiep neu co the."""
    cols = insertable_columns(t)
    exprs, kinds, names, limits = _select_plan(cols, opts)
    if not names:
        return
    full = qn(t["schema"], t["table"])
    label = t["schema"] + "." + t["table"]
    has_identity = any(c["is_identity"] for c in cols)

    # Vi tri cac cot khoa trong ket qua - de biet doc tiep tu dau khi gap loi.
    key_cols = _clustered_key(t)
    key_pos = [names.index(k) for k in key_cols if k in names]
    can_resume = len(key_pos) == len(key_cols) and bool(key_cols)

    top = "TOP (" + str(opts.max_rows_per_table) + ") " if opts.max_rows_per_table else ""
    base_select = "SELECT " + top + ", ".join(exprs) + " FROM " + full
    col_list = "(" + ", ".join(q(n) for n in names) + ")"

    state = {"header": False, "count": 0, "last_key": None, "rejected": 0}

    def flush(rows):
        if can_resume:
            state["last_key"] = [rows[-1][p] for p in key_pos]

        good = [r for r in rows if _row_fits(r, limits)]
        if len(good) != len(rows):
            for bad in rows:
                if _row_fits(bad, limits):
                    continue
                state["rejected"] += 1
                if len(rep.rejected_rows) < 2000:
                    rep.rejected_rows.append({
                        "table": label, "columns": col_list,
                        "values": "(" + ", ".join(_value_sql(v, k)
                                                  for v, k in zip(bad, kinds)) + ")",
                    })
        if not good:
            return

        if not state["header"]:
            w.write("PRINT N'  -> " + label + "';")
            if has_identity:
                w.write("SET IDENTITY_INSERT " + full + " ON;")
            state["header"] = True
        buf = ["(" + ", ".join(_value_sql(v, k) for v, k in zip(row, kinds)) + ")"
               for row in good]
        w.rows("INSERT INTO " + full + " " + col_list + " VALUES", buf)
        w.go()
        state["count"] += len(good)

    def stream(sql, args=None):
        """Doc het 1 truy van, tra ve True neu doc tron ven."""
        cur = conn.cursor()
        cur.execute(sql, *(args or []))
        while True:
            rows = cur.fetchmany(batch_rows)
            if not rows:
                return True
            flush(rows)

    completed = False
    failure = ""
    try:
        completed = stream(base_select)
    except Exception as exc:
        failure = _short(exc)

    # Doc lai tu ban ghi cuoi cung doc duoc. Truy van co WHERE khoa se di thang tu
    # goc cay chi muc xuong, nen co co hoi khong cham lai trang bi hong.
    attempts = 0
    while not completed and can_resume and state["last_key"] and attempts < 3:
        attempts += 1
        before = state["count"]
        sql = (base_select + " WHERE " + _keyset_where(key_cols) +
               " ORDER BY " + ", ".join(q(c) for c in key_cols))
        try:
            completed = stream(sql, _keyset_args(state["last_key"]))
        except Exception as exc:
            failure = _short(exc)
        if state["count"] == before:
            break          # khong tien them duoc dong nao nua

    if state["header"] and has_identity:
        w.write("SET IDENTITY_INSERT " + full + " OFF;")
        w.go()

    if not completed:
        estimate = t["row_count"]
        msg = (label + ": chỉ đọc được " + f"{state['count']:,}" + " dòng trên ước tính " +
               f"{estimate:,}" + " vì gặp trang dữ liệu bị hỏng. " + failure)
        rep.warnings.append(msg)
        w.write("-- CẢNH BÁO: " + msg)
        w.write()

    if state["rejected"]:
        msg = (label + ": bỏ qua " + f"{state['rejected']:,}" + " dòng có giá trị vượt "
               "kích thước cột, đây là dữ liệu rác từ trang hỏng. Xem file *_dong-loi.sql.")
        rep.warnings.append(msg)
        w.write("-- CANH BAO: " + msg)
        w.write()

    rep.rows += state["count"]
    rep.rejected += state["rejected"]
    rep.table_stats.append({
        "table": label, "rows": state["count"], "rejected": state["rejected"],
        "expected": t["row_count"], "complete": completed,
    })


def _short(exc: Exception) -> str:
    text = str(exc)
    marker = "[SQL Server]"
    if marker in text:
        text = text.split(marker)[-1]
    return text.split("(SQLFetch")[0].strip()[:200]


def _write_keys(w: ScriptWriter, conn, tables: list, rep: Report, progress) -> None:
    has_any = any(t["keys"] for t in tables)
    if not has_any:
        return
    w.section("6. Khoá chính và ràng buộc UNIQUE")
    for t in tables:
        for key in t["keys"]:
            cols = ", ".join(q(c) + (" DESC" if desc else " ASC")
                             for c, desc in key["columns"])
            kind = "PRIMARY KEY" if key["type"] == "PK" else "UNIQUE"
            clustered = "CLUSTERED" if "CLUSTERED" == key["type_desc"] else "NONCLUSTERED"
            stmt = ("ALTER TABLE " + qn(t["schema"], t["table"]) + " ADD CONSTRAINT " +
                    q(key["name"]) + " " + kind + " " + clustered + " (" + cols + ");")

            dups = find_duplicate_keys(conn, t, key)
            if dups:
                # Du lieu cuu ho tu file hong co the co ban ghi trung -> tao khoa se loi.
                # Ghi ra dang chu thich de file van import duoc, kem canh bao ro rang.
                msg = (t["schema"] + "." + t["table"] + ": khoá chính " + key["name"] + " có " +
                       str(dups) + " bộ giá trị bị trùng nên đã tạm tắt ràng buộc này.")
                rep.warnings.append(msg)
                w.write("-- CANH BAO: " + msg)
                w.write("-- Xử lý trùng lặp rồi bỏ dấu chú thích ở dòng dưới để bật lại.")
                w.write("-- " + stmt)
            else:
                w.write(stmt)
            w.go()


def _write_indexes(w: ScriptWriter, tables: list) -> None:
    if not any(t["indexes"] for t in tables):
        return
    w.section("7. Index")
    for t in tables:
        for idx in t["indexes"]:
            unique = "UNIQUE " if idx["is_unique"] else ""
            kind = "CLUSTERED" if idx["type_desc"] == "CLUSTERED" else "NONCLUSTERED"
            cols = ", ".join(q(c) + (" DESC" if desc else " ASC")
                             for c, desc in idx["key_columns"])
            stmt = ("CREATE " + unique + kind + " INDEX " + q(idx["name"]) + " ON " +
                    qn(t["schema"], t["table"]) + " (" + cols + ")")
            if idx["included_columns"]:
                stmt += " INCLUDE (" + ", ".join(q(c) for c in idx["included_columns"]) + ")"
            if idx["filter"]:
                stmt += " WHERE " + idx["filter"]
            w.write(stmt + ";")
            w.go()


def _write_checks(w: ScriptWriter, model: dict) -> None:
    if not model["check_constraints"]:
        return
    w.section("8. Ràng buộc CHECK")
    for c in model["check_constraints"]:
        keyword = "WITH NOCHECK" if c["is_not_trusted"] else "WITH CHECK"
        w.write("ALTER TABLE " + qn(c["schema"], c["table"]) + " " + keyword +
                " ADD CONSTRAINT " + q(c["name"]) + " CHECK " + c["definition"] + ";")
        if c["is_disabled"]:
            w.write("ALTER TABLE " + qn(c["schema"], c["table"]) +
                    " NOCHECK CONSTRAINT " + q(c["name"]) + ";")
        w.go()


def _write_foreign_keys(w: ScriptWriter, conn, model: dict, rep: Report) -> None:
    if not model["foreign_keys"]:
        return
    w.section("9. Khoá ngoại")
    for fk in model["foreign_keys"]:
        orphans = find_orphan_rows(conn, fk)
        # Con mo coi -> WITH CHECK se loi. Dung WITH NOCHECK de import chay tron.
        keyword = "WITH NOCHECK" if (orphans or fk["is_not_trusted"]) else "WITH CHECK"
        if orphans:
            msg = (fk["parent_schema"] + "." + fk["parent_table"] + ": khoá ngoại " +
                   fk["name"] + " có " + str(orphans) + " dòng trỏ tới bản ghi cha không "
                   "còn tồn tại nên được tạo ở chế độ không kiểm tra.")
            rep.warnings.append(msg)
            w.write("-- CANH BAO: " + msg)
        cols = ", ".join(q(c) for c in fk["parent_columns"])
        refs = ", ".join(q(c) for c in fk["ref_columns"])
        stmt = ("ALTER TABLE " + qn(fk["parent_schema"], fk["parent_table"]) + " " +
                keyword + " ADD CONSTRAINT " + q(fk["name"]) + " FOREIGN KEY (" + cols +
                ") REFERENCES " + qn(fk["ref_schema"], fk["ref_table"]) + " (" + refs + ")")
        for action, clause in ((fk["on_delete"], "ON DELETE"), (fk["on_update"], "ON UPDATE")):
            if action and action != "NO_ACTION":
                stmt += " " + clause + " " + action.replace("_", " ")
        w.write(stmt + ";")
        if fk["is_disabled"]:
            w.write("ALTER TABLE " + qn(fk["parent_schema"], fk["parent_table"]) +
                    " NOCHECK CONSTRAINT " + q(fk["name"]) + ";")
        w.go()


def _write_modules(w: ScriptWriter, model: dict) -> None:
    if not model["modules"]:
        return
    w.section("10. View, hàm, thủ tục, trigger")
    for m in model["modules"]:
        if not m["definition"]:
            w.write("-- Bỏ qua " + m["name"] + ": mã nguồn bị mã hoá (WITH ENCRYPTION).")
            continue
        obj_type = {"VIEW": "V", "SQL_STORED_PROCEDURE": "P", "SQL_SCALAR_FUNCTION": "FN",
                    "SQL_INLINE_TABLE_VALUED_FUNCTION": "IF",
                    "SQL_TABLE_VALUED_FUNCTION": "TF", "SQL_TRIGGER": "TR"}.get(m["type_desc"])
        if obj_type:
            w.write("IF OBJECT_ID(N'" + (m["schema"] + "." + m["name"]).replace("'", "''") +
                    "', N'" + obj_type + "') IS NOT NULL")
            drop = {"V": "VIEW", "P": "PROCEDURE", "TR": "TRIGGER"}.get(obj_type, "FUNCTION")
            w.write("    DROP " + drop + " " + qn(m["schema"], m["name"]) + ";")
            w.go()
        # Ma nguon phai la cau lenh dau tien cua batch -> ke GO truoc va sau.
        w.write(m["definition"].rstrip())
        w.go()


def _write_reseed(w: ScriptWriter, tables: list) -> None:
    targets = [t for t in tables if any(c["is_identity"] for c in t["columns"])]
    if not targets:
        return
    w.section("11. Đặt lại bộ đếm IDENTITY")
    for t in targets:
        w.write("DBCC CHECKIDENT (N'" + (t["schema"] + "." + t["table"]).replace("'", "''") +
                "', RESEED) WITH NO_INFOMSGS;")
    w.go()


def convert_mdf(mdf_path: str, out_path: str, server: str = "",
                opts: Options | None = None, progress=_noop,
                keep_attached: bool = False) -> Report:
    """Duong di day du: .mdf  ->  .sql. Tra ve Report de hien thi cho nguoi dung."""
    opts = opts or Options()
    rep = Report(out_path=out_path)

    if not server:
        found = dbconn.discover_instances()
        running = [i for i in found if i.running]
        if not running:
            raise dbconn.Mdf2SqlError(
                "Không tìm thấy SQL Server nào đang chạy trên máy. Hãy cài SQL Server "
                "Express hoặc LocalDB rồi thử lại.")
        server = running[0].server
        kind = running[0].kind
    else:
        kind = "localdb" if server.lower().startswith("(localdb)") else "service"

    progress("connect", 0, 1, "Kết nối " + server + "...")
    conn = dbconn.connect(server)

    progress("probe", 0, 1, "Đọc thông tin bên trong file .mdf...")
    info = attacher.probe_primary_file(conn, os.path.abspath(mdf_path))
    boot = dbconn.mdf_boot_info(mdf_path)
    source_name = info.get("db_name") or boot.get("db_name") or "database"

    workspace = attacher.default_workspace()
    os.makedirs(workspace, exist_ok=True)
    for note in attacher.grant_access(workspace, kind, server):
        rep.details.append(note)

    progress("copy", 0, 1, "Sao chép file ra vùng làm việc, giữ nguyên file gốc...")
    work_mdf, files = attacher.stage_files(mdf_path, workspace)

    temp_db = attacher.safe_db_name(source_name)
    attached = None
    scratch = list(files)          # moi file tam da tao ra, de don dep sau cung
    try:
        res = attacher.attach(conn, work_mdf, files, temp_db,
                              progress=lambda m: progress("attach", 0, 1, m),
                              logical=info.get("files"))
        attached = temp_db
        scratch.extend(res.work_files)
        rep.strategy = res.strategy
        rep.repaired = res.repaired
        rep.warnings.extend(res.warnings)
        rep.details.extend(res.details)

        say = lambda m: progress("check", 0, 1, m)
        rep.integrity = attacher.check_integrity(conn, temp_db, progress=say)
        if rep.integrity.get("errors") and opts.auto_repair:
            rep.warnings.append(
                "File có " + str(rep.integrity["errors"]) + " lỗi cấu trúc, "
                "tool đang tự động sửa bằng DBCC CHECKDB.")
            fixed = attacher.repair(conn, temp_db, progress=say)
            rep.repaired = True
            rep.integrity = attacher.check_integrity(conn, temp_db, progress=say)
            if rep.integrity.get("errors"):
                rep.warnings.append(
                    "Sau khi sửa vẫn còn " + str(rep.integrity["errors"]) +
                    " lỗi, một số bảng có thể xuất thiếu dòng. Xem cột trạng thái "
                    "trong bảng kết quả.")
            else:
                rep.warnings.append("Đã sửa xong, kiểm tra lại không còn lỗi cấu trúc.")
            for m in fixed.get("messages", [])[:3]:
                rep.details.append("DBCC: " + m[:200])
        elif rep.integrity.get("errors"):
            rep.warnings.append(
                "Kiểm tra thấy " + str(rep.integrity["errors"]) +
                " lỗi toàn vẹn nhưng tuỳ chọn tự động sửa đang tắt.")

        progress("schema", 0, 1, "Đọc cấu trúc database...")
        db_conn = dbconn.connect(server, database=temp_db)
        try:
            model = introspect.read_all(db_conn)
            model["database"]["name"] = source_name
            rep.db_name = source_name
            if not opts.target_db:
                opts.target_db = source_name

            progress("emit", 0, 1, "Đang sinh file .sql...")
            out = generate_script(db_conn, model, out_path, opts, progress)
            out.strategy = rep.strategy
            out.repaired = rep.repaired
            out.integrity = rep.integrity
            out.warnings = rep.warnings + out.warnings
            out.details = rep.details + out.details
            out.db_name = source_name
            return out
        finally:
            db_conn.close()
    finally:
        if not keep_attached:
            progress("cleanup", 0, 1, "Gỡ database tạm và dọn dẹp bản sao...")
            if attached:
                attacher.detach(conn, attached)
            attacher.cleanup(scratch)
        conn.close()
