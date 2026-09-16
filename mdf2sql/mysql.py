"""Sinh script MySQL / MariaDB tu schema doc duoc cua SQL Server.

Dung khi muon nap du lieu len phpMyAdmin, XAMPP, Laragon hay hosting dung MySQL.
File sinh ra khong chua cu phap rieng cua SQL Server: khong co dau ngoac vuong,
khong co GO, khong co tien to N truoc chuoi.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import re
import uuid

from .introspect import is_binary_blob

# Gioi han cua MySQL.
MAX_IDENT = 64                 # do dai toi da cua ten bang, ten cot, ten khoa
MAX_KEY_BYTES = 3000           # do dai toi da cua mot khoa InnoDB (3072, tru hao)
MAX_ROW_BYTES = 60000          # do dai toi da mot dong InnoDB (65535, tru hao)
BYTES_PER_CHAR = 4             # utf8mb4

NL = "\r\n"

# Ky tu phai thoat trong chuoi MySQL o che do mac dinh (dau gach cheo nguoc co hieu luc).
_ESCAPE = {
    "\\": "\\\\",
    "'": "\\'",
    "\n": "\\n",
    "\r": "\\r",
    "\x00": "\\0",
    "\x1a": "\\Z",
}
_ESCAPE_RE = re.compile("[" + re.escape("".join(_ESCAPE)) + "]")


def ident(name: str) -> str:
    """Boc ten doi tuong trong dau huyen nguoc, escape dau huyen nguoc thanh doi."""
    return "`" + str(name).replace("`", "``") + "`"


def shorten(name: str, used: set) -> str:
    """Rut ten ve toi da 64 ky tu ma khong trung voi ten da dung."""
    name = str(name)
    if len(name) <= MAX_IDENT and name.lower() not in used:
        used.add(name.lower())
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    base = name[:MAX_IDENT - 7].rstrip("_")
    out = base + "_" + digest
    used.add(out.lower())
    return out


def table_name(table: dict) -> str:
    """MySQL khong co schema long trong database, nen gop schema vao ten bang."""
    if (table["schema"] or "dbo").lower() == "dbo":
        return table["table"]
    return table["schema"] + "_" + table["table"]


def escape(text: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPE[m.group()], text)


# ------------------------------------------------------------------ kieu du lieu

def column_type(col: dict) -> str:
    """Doi kieu du lieu SQL Server sang kieu MySQL tuong duong gan nhat."""
    tn = (col["type_name"] or "").lower()
    max_len = col["max_length"]
    precision = col["precision"] or 0
    scale = col["scale"] or 0

    if tn == "bit":
        return "TINYINT(1)"
    if tn == "tinyint":
        return "TINYINT UNSIGNED"          # tinyint cua SQL Server la 0..255
    if tn == "smallint":
        return "SMALLINT"
    if tn == "int":
        return "INT"
    if tn == "bigint":
        return "BIGINT"
    if tn in ("decimal", "numeric"):
        return "DECIMAL(" + str(min(precision, 65)) + "," + str(min(scale, 30)) + ")"
    if tn == "money":
        return "DECIMAL(19,4)"
    if tn == "smallmoney":
        return "DECIMAL(10,4)"
    if tn == "float":
        return "DOUBLE" if precision > 24 else "FLOAT"
    if tn == "real":
        return "FLOAT"
    if tn == "date":
        return "DATE"
    if tn in ("datetime", "smalldatetime"):
        return "DATETIME(3)"               # datetime cua SQL Server co mili giay
    if tn == "datetime2":
        return "DATETIME(" + str(min(scale, 6)) + ")"
    if tn == "time":
        return "TIME(" + str(min(scale, 6)) + ")"
    if tn == "datetimeoffset":
        return "VARCHAR(34)"               # MySQL khong co kieu kem mui gio
    if tn == "uniqueidentifier":
        return "CHAR(36)"
    if tn in ("text", "ntext", "xml", "sql_variant"):
        return "LONGTEXT"
    if tn == "image":
        return "LONGBLOB"

    if tn in ("char", "nchar"):
        n = max_len // 2 if tn == "nchar" else max_len
        return "CHAR(" + str(n) + ")" if 0 < n <= 255 else "VARCHAR(" + str(max(n, 1)) + ")"
    if tn in ("varchar", "nvarchar"):
        if max_len == -1:
            return "LONGTEXT"
        n = max_len // 2 if tn == "nvarchar" else max_len
        return "VARCHAR(" + str(max(n, 1)) + ")" if n <= 1000 else "TEXT"
    if tn == "binary":
        return "BINARY(" + str(max_len) + ")" if 0 < max_len <= 255 else "LONGBLOB"
    if tn == "varbinary":
        if max_len == -1 or max_len > 60000:
            return "LONGBLOB"
        return "VARBINARY(" + str(max(max_len, 1)) + ")"
    if tn in ("geometry", "geography", "hierarchyid"):
        return "LONGBLOB"
    return "LONGTEXT"


def _declared_bytes(mysql_type: str) -> int:
    """Uoc tinh so byte mot cot chiem trong gioi han do dai dong cua InnoDB."""
    m = re.match(r"(VAR)?CHAR\((\d+)\)", mysql_type)
    if m:
        return int(m.group(2)) * BYTES_PER_CHAR + 2
    m = re.match(r"(VAR)?BINARY\((\d+)\)", mysql_type)
    if m:
        return int(m.group(2)) + 2
    if mysql_type.startswith(("LONGTEXT", "LONGBLOB", "TEXT", "BLOB")):
        return 12                          # chi luu con tro trong dong
    return 8


def _key_bytes(mysql_type: str) -> int:
    """So byte mot cot chiem khi nam trong khoa."""
    m = re.match(r"(VAR)?CHAR\((\d+)\)", mysql_type)
    if m:
        return int(m.group(2)) * BYTES_PER_CHAR
    m = re.match(r"(VAR)?BINARY\((\d+)\)", mysql_type)
    if m:
        return int(m.group(2))
    if mysql_type.startswith(("LONGTEXT", "TEXT")):
        return 10000                       # phai dung tien to, khong the danh chi muc ca cot
    return 8


# ------------------------------------------------------------------ gia tri

def literal(value, kind: str = "") -> str:
    """Doi 1 gia tri Python thanh literal MySQL an toan."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return "NULL" if (value.is_nan() or value.is_infinite()) else str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return "NULL"
        return repr(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return "0x" + raw.hex().upper() if raw else "''"
    if isinstance(value, datetime.datetime):
        if value.year < 1000:
            return "NULL"                  # ngoai pham vi DATETIME cua MySQL
        text = value.strftime("%Y-%m-%d %H:%M:%S")
        if value.microsecond:
            text += "." + str(value.microsecond).zfill(6)
        if value.tzinfo is not None:
            offset = value.strftime("%z")
            text += offset[:3] + ":" + offset[3:]
        return "'" + text + "'"
    if isinstance(value, datetime.date):
        return "NULL" if value.year < 1000 else "'" + value.strftime("%Y-%m-%d") + "'"
    if isinstance(value, datetime.time):
        text = value.strftime("%H:%M:%S")
        if value.microsecond:
            text += "." + str(value.microsecond).zfill(6)
        return "'" + text + "'"
    if isinstance(value, uuid.UUID):
        return "'" + str(value) + "'"
    return "'" + escape(str(value)) + "'"


def value_sql(value, kind: str) -> str:
    """Doi 1 o du lieu doc duoc thanh doan SQL. kind den tu reader.select_plan."""
    if value is None:
        return "NULL"
    if kind == "text":                     # datetime2 / time doc duoi dang chuoi ISO
        return "'" + escape(str(value).replace("T", " ")) + "'"
    if kind.startswith("udt:"):
        return "0x" + bytes(value).hex().upper()
    return literal(value, kind)


def default_value(col: dict, mysql_type: str):
    """Doi rang buoc DEFAULT cua SQL Server sang MySQL. None = bo qua."""
    raw = col.get("default_definition")
    if not raw:
        return None
    if mysql_type.startswith(("LONGTEXT", "LONGBLOB", "TEXT", "BLOB")):
        return None                        # MySQL cu khong cho DEFAULT tren kieu nay

    text = str(raw).strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()

    low = text.lower()
    if low in ("getdate()", "sysdatetime()", "current_timestamp", "getutcdate()"):
        return "CURRENT_TIMESTAMP(3)" if mysql_type.startswith("DATETIME") else None
    if low in ("newid()", "newsequentialid()"):
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return text
    m = re.fullmatch(r"N?'(.*)'", text, re.S)
    if m:
        return "'" + escape(m.group(1).replace("''", "'")) + "'"
    return None                            # bieu thuc phuc tap: bo qua cho an toan


# ------------------------------------------------------------------ cot

def data_columns(table: dict, skip_blobs: bool) -> list:
    """Cot se co mat trong CREATE TABLE va trong INSERT.

    Khac ban T-SQL o cho cot tinh toan duoc giu lai thanh cot thuong kem du lieu,
    vi bieu thuc tinh toan cua SQL Server khong dich sang MySQL duoc.
    """
    out = []
    for c in table["columns"]:
        if (c["type_name"] or "").lower() in ("timestamp", "rowversion"):
            continue
        if skip_blobs and is_binary_blob(c):
            continue
        out.append(c)
    return out


def column_ddl(col: dict, names: dict) -> str:
    """Sinh 1 dong dinh nghia cot."""
    mysql_type = column_type(col)
    parts = [ident(names[col["name"]]), mysql_type]
    parts.append("NULL" if col["is_nullable"] else "NOT NULL")
    default = default_value(col, mysql_type)
    if default is not None:
        parts.append("DEFAULT " + default)
    if col["is_computed"]:
        parts.append("COMMENT 'cot tinh toan trong SQL Server, o day luu gia tri tinh san'")
    return " ".join(parts)


# ------------------------------------------------------------------ dat ten

def build_names(model: dict, skip_blobs: bool):
    """Dung truoc bang anh xa ten SQL Server sang ten MySQL hop le (toi da 64 ky tu)."""
    used_tables: set = set()
    tables: dict = {}
    columns: dict = {}
    for t in model["tables"]:
        key = (t["schema"], t["table"])
        tables[key] = shorten(table_name(t), used_tables)
        used_cols: set = set()
        columns[key] = {c["name"]: shorten(c["name"], used_cols)
                        for c in data_columns(t, skip_blobs)}
    return tables, columns


def key_spec(cols: list, colmap: dict, types: dict):
    """Sinh danh sach cot cho khoa, tu cat tien to neu khoa qua dai.

    Tra ve (chuoi cot, co phai cat tien to khong).
    """
    sizes = [_key_bytes(types[c]) for c, _ in cols]
    total = sum(sizes)
    if total <= MAX_KEY_BYTES:
        return ", ".join(ident(colmap[c]) for c, _ in cols), False

    budget = MAX_KEY_BYTES // max(len(cols), 1)
    parts = []
    for (c, _), size in zip(cols, sizes):
        if size <= budget:
            parts.append(ident(colmap[c]))
        else:
            chars = max(1, budget // BYTES_PER_CHAR)
            parts.append(ident(colmap[c]) + "(" + str(chars) + ")")
    return ", ".join(parts), True


# ------------------------------------------------------------------ sinh script

def write_script(conn, model, out_path, opts, rep, progress, fh) -> None:
    """Ghi toan bo script MySQL vao fh da mo san."""
    from .emit import ScriptWriter, find_duplicate_keys, find_orphan_rows
    from . import reader

    w = ScriptWriter(fh)

    excluded = {e.lower() for e in opts.exclude_tables}
    tables = [t for t in model["tables"]
              if (t["schema"] + "." + t["table"]).lower() not in excluded]
    rep.tables = len(tables)

    tmap, cmap = build_names(model, opts.skip_blobs)
    target_db = opts.target_db or model["database"].get("name") or "database"

    _header(w, model, tables, target_db)

    if opts.create_database:
        w.section("1. Tao database")
        w.write("CREATE DATABASE IF NOT EXISTS " + ident(target_db) +
                " DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        w.write("USE " + ident(target_db) + ";")
        w.write()

    _create_tables(w, tables, tmap, cmap, opts)

    if opts.include_data:
        _write_data(w, conn, tables, tmap, cmap, opts, rep, progress, reader)
    else:
        rep.warnings.append("Da chon chi xuat cau truc, file khong chua du lieu.")

    _write_keys(w, conn, tables, tmap, cmap, opts, rep, find_duplicate_keys)
    _write_indexes(w, tables, tmap, cmap, opts, rep)
    _write_foreign_keys(w, conn, model, tmap, cmap, opts, rep, find_orphan_rows)
    _write_unsupported(w, model, rep)

    w.section("Ket thuc")
    w.write("SET FOREIGN_KEY_CHECKS = @m2s_fk;")
    w.write("SET UNIQUE_CHECKS = @m2s_uc;")
    w.write("SET SQL_MODE = @m2s_mode;")
    w.write()


def _header(w, model: dict, tables: list, target_db: str) -> None:
    total = sum(t["row_count"] for t in tables)
    w.write("-- " + "=" * 70)
    w.write("-- Script sinh boi mdf2sql: file .mdf cua SQL Server sang MySQL / MariaDB")
    w.write("-- Database goc : " + str(model["database"].get("name")))
    w.write("-- Database dich: " + target_db)
    w.write("-- So bang      : " + str(len(tables)))
    w.write("-- So dong (uoc): " + f"{total:,}")
    w.write("-- Thoi diem    : " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    w.write("--")
    w.write("-- Cach nap:")
    w.write("--   - phpMyAdmin: tab Import, chon file nay. File lon thi nen .gz roi tai len.")
    w.write("--   - Dong lenh : mysql -u root -p ten_database < ten_file.sql")
    w.write("--")
    w.write("-- Thu tu trong file: tao bang, do du lieu, roi moi toi khoa chinh, chi muc")
    w.write("-- va khoa ngoai. Nho vay khong vuong thu tu phu thuoc giua cac bang.")
    w.write("-- " + "=" * 70)
    w.write()
    w.write("SET NAMES utf8mb4;")
    w.write("SET @m2s_mode = @@SQL_MODE;")
    w.write("SET @m2s_fk = @@FOREIGN_KEY_CHECKS;")
    w.write("SET @m2s_uc = @@UNIQUE_CHECKS;")
    # Tat che do nghiem ngat: du lieu cuu ho tu file hong co the co gia tri la,
    # o che do nghiem ngat thi ca lenh import se dung lai giua chung.
    w.write("SET SQL_MODE = 'NO_ENGINE_SUBSTITUTION';")
    w.write("SET FOREIGN_KEY_CHECKS = 0;")
    w.write("SET UNIQUE_CHECKS = 0;")
    w.write()


def _create_tables(w, tables: list, tmap: dict, cmap: dict, opts) -> None:
    w.section("2. Tao bang")
    for t in tables:
        key = (t["schema"], t["table"])
        name = tmap[key]
        colmap = cmap[key]
        cols = data_columns(t, opts.skip_blobs)

        if opts.drop_if_exists:
            w.write("DROP TABLE IF EXISTS " + ident(name) + ";")

        types = {c["name"]: column_type(c) for c in cols}
        row_bytes = sum(_declared_bytes(types[c["name"]]) for c in cols)
        if row_bytes > MAX_ROW_BYTES:
            # Dong qua dai cho InnoDB, day cac cot chuoi lon sang TEXT.
            for c in sorted(cols, key=lambda x: -_declared_bytes(types[x["name"]])):
                if row_bytes <= MAX_ROW_BYTES:
                    break
                if types[c["name"]].startswith("VARCHAR("):
                    row_bytes -= _declared_bytes(types[c["name"]]) - 12
                    types[c["name"]] = "TEXT"

        lines = []
        for c in cols:
            mysql_type = types[c["name"]]
            parts = [ident(colmap[c["name"]]), mysql_type]
            parts.append("NULL" if c["is_nullable"] else "NOT NULL")
            default = default_value(c, mysql_type)
            if default is not None:
                parts.append("DEFAULT " + default)
            lines.append("  " + " ".join(parts))

        w.write("CREATE TABLE " + ident(name) + " (")
        w.write(("," + NL).join(lines))
        w.write(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;")
        w.write()


def _write_data(w, conn, tables, tmap, cmap, opts, rep, progress, reader) -> None:
    w.section("3. Du lieu")
    batch = max(1, min(opts.batch_rows, 1000))
    for i, t in enumerate(tables, 1):
        key = (t["schema"], t["table"])
        label = t["schema"] + "." + t["table"]
        progress("data", i, len(tables),
                 "Dang xuat " + label + " (" + f"{t['row_count']:,}" + " dong)")

        cols = data_columns(t, opts.skip_blobs)
        exprs, kinds, names, limits = reader.select_plan(cols, opts.skip_blobs)
        if not names:
            continue
        colmap = cmap[key]
        col_list = "(" + ", ".join(ident(colmap[n]) for n in names) + ")"
        target = ident(tmap[key])
        wrote = [False]

        def on_rows(rows, target=target, col_list=col_list, kinds=kinds, wrote=wrote):
            wrote[0] = True
            buf = ["(" + ", ".join(value_sql(v, k) for v, k in zip(row, kinds)) + ")"
                   for row in rows]
            w.rows("INSERT INTO " + target + " " + col_list + " VALUES", buf)

        keep = max(0, 2000 - len(rep.rejected_rows))
        res = reader.read_table(conn, t, exprs, names, limits, batch, on_rows,
                                max_rows=opts.max_rows_per_table, keep_bad=keep)
        for bad in res.bad_rows:
            rep.rejected_rows.append({
                "table": label, "columns": col_list,
                "values": "(" + ", ".join(value_sql(v, k)
                                          for v, k in zip(bad, kinds)) + ")",
            })
        reader.note_result(w, t, label, res, rep)
        if wrote[0]:
            w.write()


def _write_keys(w, conn, tables, tmap, cmap, opts, rep, find_duplicate_keys) -> None:
    if not any(t["keys"] for t in tables):
        return
    w.section("4. Khoa chinh va rang buoc UNIQUE")
    for t in tables:
        key = (t["schema"], t["table"])
        colmap = cmap[key]
        cols = data_columns(t, opts.skip_blobs)
        types = {c["name"]: column_type(c) for c in cols}
        label = t["schema"] + "." + t["table"]

        for kc in t["keys"]:
            if any(c not in colmap for c, _ in kc["columns"]):
                continue                    # khoa dung cot da bi bo qua
            spec, prefixed = key_spec(kc["columns"], colmap, types)
            is_pk = kc["type"] == "PK"

            dups = find_duplicate_keys(conn, t, kc)
            if dups:
                msg = (label + ": khoa " + kc["name"] + " co " + str(dups) +
                       " bo gia tri bi trung nen da tam tat rang buoc nay.")
                rep.warnings.append(msg)
                w.write("-- CANH BAO: " + msg)
                w.write("-- ALTER TABLE " + ident(tmap[key]) + " ADD " +
                        ("PRIMARY KEY" if is_pk else "UNIQUE") + " (" + spec + ");")
                w.write()
                continue

            if prefixed:
                # Khoa dai hon gioi han InnoDB, ha xuong chi muc thuong de van nap duoc.
                msg = (label + ": khoa " + kc["name"] + " dai qua gioi han cua MySQL nen "
                       "duoc tao thanh chi muc thuong thay vi khoa chinh.")
                rep.warnings.append(msg)
                w.write("-- CANH BAO: " + msg)
                w.write("ALTER TABLE " + ident(tmap[key]) + " ADD INDEX " +
                        ident(kc["name"][:MAX_IDENT]) + " (" + spec + ");")
            elif is_pk:
                w.write("ALTER TABLE " + ident(tmap[key]) + " ADD PRIMARY KEY (" + spec + ");")
            else:
                w.write("ALTER TABLE " + ident(tmap[key]) + " ADD CONSTRAINT " +
                        ident(kc["name"][:MAX_IDENT]) + " UNIQUE (" + spec + ");")
            w.write()

            # AUTO_INCREMENT chi gan duoc sau khi cot da nam trong mot khoa.
            if is_pk and not prefixed and len(kc["columns"]) == 1:
                first = kc["columns"][0][0]
                col = next((c for c in cols if c["name"] == first), None)
                if col is not None and col["is_identity"]:
                    _auto_increment(w, conn, t, tmap[key], colmap[first], col)


def _auto_increment(w, conn, t: dict, mysql_table: str, mysql_col: str, col: dict) -> None:
    """Bien cot IDENTITY thanh AUTO_INCREMENT va dat lai bo dem."""
    w.write("ALTER TABLE " + ident(mysql_table) + " MODIFY " + ident(mysql_col) + " " +
            column_type(col) + " NOT NULL AUTO_INCREMENT;")
    try:
        src = ("[" + t["schema"].replace("]", "]]") + "].[" +
               t["table"].replace("]", "]]") + "]")
        biggest = conn.cursor().execute(
            "SELECT MAX([" + col["name"].replace("]", "]]") + "]) FROM " + src).fetchone()[0]
    except Exception:
        biggest = None
    if biggest is not None:
        w.write("ALTER TABLE " + ident(mysql_table) + " AUTO_INCREMENT = " +
                str(int(biggest) + 1) + ";")
    w.write()


def _write_indexes(w, tables, tmap, cmap, opts, rep) -> None:
    if not any(t["indexes"] for t in tables):
        return
    w.section("5. Chi muc")
    for t in tables:
        key = (t["schema"], t["table"])
        colmap = cmap[key]
        cols = data_columns(t, opts.skip_blobs)
        types = {c["name"]: column_type(c) for c in cols}
        for idx in t["indexes"]:
            if any(c not in colmap for c, _ in idx["key_columns"]):
                continue
            if idx["filter"]:
                w.write("-- Bo qua chi muc " + idx["name"] +
                        ": MySQL khong co chi muc loc theo dieu kien.")
                continue
            spec, _ = key_spec(idx["key_columns"], colmap, types)
            unique = "UNIQUE " if idx["is_unique"] else ""
            w.write("ALTER TABLE " + ident(tmap[key]) + " ADD " + unique + "INDEX " +
                    ident(idx["name"][:MAX_IDENT]) + " (" + spec + ");")
            if idx["included_columns"]:
                w.write("--   (cac cot INCLUDE cua SQL Server khong co tuong duong, da bo)")
    w.write()


def _write_foreign_keys(w, conn, model, tmap, cmap, opts, rep, find_orphan_rows) -> None:
    if not model["foreign_keys"]:
        return
    w.section("6. Khoa ngoai")
    used: set = set()
    for fk in model["foreign_keys"]:
        pkey = (fk["parent_schema"], fk["parent_table"])
        rkey = (fk["ref_schema"], fk["ref_table"])
        if pkey not in tmap or rkey not in tmap:
            continue
        pcols, rcols = cmap[pkey], cmap[rkey]
        if any(c not in pcols for c in fk["parent_columns"]):
            continue
        if any(c not in rcols for c in fk["ref_columns"]):
            continue

        orphans = find_orphan_rows(conn, fk)
        if orphans:
            msg = (fk["parent_schema"] + "." + fk["parent_table"] + ": khoa ngoai " +
                   fk["name"] + " co " + str(orphans) + " dong tro toi ban ghi cha khong "
                   "con ton tai. MySQL khong co che do bo kiem tra nhu SQL Server nen "
                   "khoa nay duoc ghi ra dang chu thich.")
            rep.warnings.append(msg)
            w.write("-- CANH BAO: " + msg)
            prefix = "-- "
        else:
            prefix = ""

        name = shorten(fk["name"], used)
        stmt = (prefix + "ALTER TABLE " + ident(tmap[pkey]) + " ADD CONSTRAINT " +
                ident(name) + " FOREIGN KEY (" +
                ", ".join(ident(pcols[c]) for c in fk["parent_columns"]) +
                ") REFERENCES " + ident(tmap[rkey]) + " (" +
                ", ".join(ident(rcols[c]) for c in fk["ref_columns"]) + ")")
        for action, clause in ((fk["on_delete"], "ON DELETE"), (fk["on_update"], "ON UPDATE")):
            if action and action != "NO_ACTION":
                stmt += " " + clause + " " + action.replace("_", " ")
        w.write(stmt + ";")
    w.write()


def _write_unsupported(w, model: dict, rep) -> None:
    """View, thu tuc, trigger, rang buoc CHECK: ghi lai dang chu thich de doi chieu."""
    items = list(model["modules"]) + [
        {"type_desc": "CHECK", "schema": c["schema"], "name": c["name"],
         "definition": "CHECK " + str(c["definition"]) + " tren bang " + c["table"]}
        for c in model["check_constraints"]]
    if not items:
        return
    w.section("7. Phan can chuyen tay")
    rep.warnings.append(
        "Co " + str(len(items)) + " view / thu tuc / trigger / rang buoc CHECK viet bang "
        "T-SQL. MySQL dung cu phap khac nen tool ghi lai dang chu thich o cuoi file, "
        "ban can chuyen tay neu van can dung.")
    w.write("-- Nhung doi tuong duoi day viet bang T-SQL cua SQL Server.")
    w.write("-- MySQL dung cu phap khac nen khong chuyen tu dong duoc.")
    w.write()
    for item in items:
        w.write("-- ---- " + str(item.get("type_desc")) + ": " +
                str(item.get("schema")) + "." + str(item.get("name")) + " ----")
        for line in str(item.get("definition") or "").splitlines():
            w.write("-- " + line)
        w.write()
