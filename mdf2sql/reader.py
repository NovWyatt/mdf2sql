"""Doc du lieu tu database da attach, co kha nang di tiep khi gap trang dia hong.

Phan nay khong phu thuoc vao dialect dau ra: no chi doc tu SQL Server va giao
tung lo dong cho ben goi tu quyet dinh viet ra T-SQL hay MySQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .introspect import is_binary_blob

# Kieu can doc duoi dang khac de khong mat do chinh xac khi chuyen sang chuoi.
AS_TEXT = {"datetime2", "datetimeoffset", "time"}
AS_BINARY_UDT = {"geometry", "geography", "hierarchyid"}


@dataclass
class ReadResult:
    rows: int = 0                  # so dong doc duoc va hop le
    rejected: int = 0              # so dong bi loai vi vuot kich thuoc cot
    completed: bool = False        # doc het bang hay dung giua chung
    failure: str = ""              # thong bao loi cuoi cung neu khong tron ven
    bad_rows: list = field(default_factory=list)   # vai dong bi loai, de ghi ra file rieng


def size_limit(col: dict):
    """Kich thuoc toi da cua cot theo don vi ma Python dem duoc (None = khong gioi han).

    Du lieu hop le luon nam trong gioi han nay. Gia tri vuot nguong chi xuat hien khi
    doc trung trang bi hong, do la dong rac can tach ra.
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


def select_plan(cols: list, skip_blobs: bool = False):
    """Chon cach doc tung cot. Tra ve (bieu thuc SELECT, kieu, ten cot, gioi han)."""
    exprs, kinds, names, limits = [], [], [], []
    for c in cols:
        tn = (c["type_name"] or "").lower()
        name = c["name"]
        if skip_blobs and is_binary_blob(c):
            continue
        limits.append(size_limit(c))
        quoted = "[" + str(name).replace("]", "]]") + "]"
        if tn in AS_TEXT:
            exprs.append("CONVERT(varchar(34), " + quoted + ", 126)")
            kinds.append("text")
        elif tn in AS_BINARY_UDT:
            exprs.append("CAST(" + quoted + " AS varbinary(max))")
            kinds.append("udt:" + tn)
        elif tn in ("xml", "sql_variant"):
            exprs.append("CONVERT(nvarchar(max), " + quoted + ")")
            kinds.append("nvarchar")
        else:
            exprs.append(quoted)
            kinds.append(tn)
        names.append(name)
    return exprs, kinds, names, limits


def row_fits(row, limits) -> bool:
    """Dong co gia tri vuot kich thuoc cot la du lieu rac tu trang hong."""
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


def clustered_key(table: dict) -> list:
    """Cot khoa cua chi muc clustered, dung de doc tiep sau khi gap trang hong."""
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
        parts = ["[" + key_cols[j].replace("]", "]]") + "] = ?" for j in range(i)]
        parts.append("[" + key_cols[i].replace("]", "]]") + "] > ?")
        clauses.append("(" + " AND ".join(parts) + ")")
    return " OR ".join(clauses)


def _keyset_args(values: list) -> list:
    """Danh sach tham so khop voi _keyset_where."""
    args = []
    for i in range(len(values)):
        args.extend(values[:i])
        args.append(values[i])
    return args


def short_error(exc: Exception) -> str:
    text = str(exc)
    marker = "[SQL Server]"
    if marker in text:
        text = text.split(marker)[-1]
    return text.split("(SQLFetch")[0].strip()[:200]


def read_table(conn, table: dict, exprs: list, names: list, limits: list,
               batch_rows: int, on_rows, max_rows: int = 0,
               keep_bad: int = 0) -> ReadResult:
    """Doc het mot bang, goi on_rows(rows) cho tung lo dong hop le.

    Gap trang dia hong thi giu lai phan da doc va thu doc tiep tu ban ghi cuoi
    cung thay vi bo ca bang.
    """
    res = ReadResult()
    full = "[" + table["schema"].replace("]", "]]") + "].[" + \
           table["table"].replace("]", "]]") + "]"

    key_cols = clustered_key(table)
    key_pos = [names.index(k) for k in key_cols if k in names]
    can_resume = bool(key_cols) and len(key_pos) == len(key_cols)

    top = "TOP (" + str(max_rows) + ") " if max_rows else ""
    base_select = "SELECT " + top + ", ".join(exprs) + " FROM " + full
    last_key = [None]

    def handle(rows):
        if can_resume:
            last_key[0] = [rows[-1][p] for p in key_pos]
        good = []
        for row in rows:
            if row_fits(row, limits):
                good.append(row)
            else:
                res.rejected += 1
                if len(res.bad_rows) < keep_bad:
                    res.bad_rows.append(row)
        if good:
            on_rows(good)
            res.rows += len(good)

    def stream(sql, args=None):
        cur = conn.cursor()
        cur.execute(sql, *(args or []))
        while True:
            rows = cur.fetchmany(batch_rows)
            if not rows:
                return True
            handle(rows)

    try:
        res.completed = stream(base_select)
    except Exception as exc:
        res.failure = short_error(exc)

    # Truy van co WHERE khoa se di thang tu goc cay chi muc xuong, nen co co hoi
    # khong cham lai dung trang bi hong.
    attempts = 0
    while not res.completed and can_resume and last_key[0] and attempts < 3:
        attempts += 1
        before = res.rows
        sql = (base_select + " WHERE " + _keyset_where(key_cols) + " ORDER BY " +
               ", ".join("[" + c.replace("]", "]]") + "]" for c in key_cols))
        try:
            res.completed = stream(sql, _keyset_args(last_key[0]))
        except Exception as exc:
            res.failure = short_error(exc)
        if res.rows == before:
            break              # khong tien them duoc dong nao nua

    return res


def note_result(w, table: dict, label: str, res: ReadResult, rep) -> None:
    """Ghi canh bao ve ket qua doc mot bang vao ca file script lan bao cao.

    Dung chung cho moi dialect vi '--' la chu thich hop le o ca T-SQL lan MySQL.
    """
    if not res.completed:
        msg = (label + ": chỉ đọc được " + f"{res.rows:,}" + " dòng trên ước tính " +
               f"{table['row_count']:,}" + " vì gặp trang dữ liệu bị hỏng. " + res.failure)
        rep.warnings.append(msg)
        w.write("-- CẢNH BÁO: " + msg)
        w.write()

    if res.rejected:
        msg = (label + ": bỏ qua " + f"{res.rejected:,}" + " dòng có giá trị vượt "
               "kích thước cột, đây là dữ liệu rác từ trang hỏng. Xem file *_dong-loi.sql.")
        rep.warnings.append(msg)
        w.write("-- CẢNH BÁO: " + msg)
        w.write()

    rep.rows += res.rows
    rep.rejected += res.rejected
    rep.table_stats.append({
        "table": label, "rows": res.rows, "rejected": res.rejected,
        "expected": table["row_count"], "complete": res.completed,
    })
