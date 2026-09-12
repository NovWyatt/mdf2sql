"""Sinh script T-SQL (DDL + du lieu) tu schema da doc duoc.

Muc tieu so 1: file .sql sinh ra phai chay lai duoc tu dau den cuoi ma khong loi.
Vi vay script duoc sap xep theo thu tu:
    bang (khong rang buoc)  ->  do du lieu  ->  khoa chinh  ->  index
    ->  check  ->  khoa ngoai  ->  view / thu tuc / trigger
Nho do khong bao gio vuong thu tu phu thuoc giua cac bang.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from .introspect import NON_INSERTABLE, is_binary_blob, render_type

# Gioi han cua T-SQL: 1 cau INSERT ... VALUES toi da 1000 dong.
MAX_ROWS_PER_INSERT = 1000
# Chuoi dai hon nguong nay se duoc noi tu nhieu manh de tranh bi cat ngam.
SAFE_LITERAL_CHARS = 3500


def q(name) -> str:
    """Boc ten doi tuong trong dau ngoac vuong."""
    return "[" + str(name).replace("]", "]]") + "]"


def qn(schema, name) -> str:
    return q(schema) + "." + q(name)


def _str_literal(text: str, unicode_prefix: bool = True) -> str:
    """Boc chuoi thanh literal T-SQL, tu dong cat nho neu qua dai.

    SQL Server ngam hieu literal chuoi la varchar(8000)/nvarchar(4000); chuoi dai
    hon se bi cat mat khi do vao cot varchar(max). Noi nhieu manh bang CAST + '+'
    giu duoc nguyen ven noi dung.
    """
    prefix = "N" if unicode_prefix else ""
    escaped = text.replace("'", "''")
    if len(escaped) <= SAFE_LITERAL_CHARS:
        return prefix + "'" + escaped + "'"

    chunks, start = [], 0
    while start < len(escaped):
        end = min(start + SAFE_LITERAL_CHARS, len(escaped))
        # Khong cat giua cap dau nhay da escape ('').
        while end < len(escaped) and escaped[end - 1] == "'" and escaped[end] == "'":
            end += 1
        chunks.append(escaped[start:end])
        start = end
    target = "nvarchar(max)" if unicode_prefix else "varchar(max)"
    parts = ["CAST(" + prefix + "'" + chunks[0] + "' AS " + target + ")"]
    parts += [prefix + "'" + c + "'" for c in chunks[1:]]
    return " + ".join(parts)


def literal(value, type_name: str = "") -> str:
    """Doi 1 gia tri Python thanh literal T-SQL an toan."""
    if value is None:
        return "NULL"

    tn = (type_name or "").lower()

    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, decimal.Decimal):
        if value.is_nan() or value.is_infinite():
            return "NULL"
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return "NULL"
        return repr(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex().upper()
    if isinstance(value, datetime.datetime):
        text = value.strftime("%Y-%m-%dT%H:%M:%S")
        if value.microsecond:
            # datetime cu chi luu toi mili giay; datetime2 giu du 6 chu so.
            digits = 3 if tn == "datetime" else 6
            frac = str(value.microsecond).zfill(6)[:digits]
            text += "." + frac
        if value.tzinfo is not None:
            offset = value.strftime("%z")
            text += offset[:3] + ":" + offset[3:]
        return "'" + text + "'"
    if isinstance(value, datetime.date):
        return "'" + value.strftime("%Y-%m-%d") + "'"
    if isinstance(value, datetime.time):
        text = value.strftime("%H:%M:%S")
        if value.microsecond:
            text += "." + str(value.microsecond).zfill(6)
        return "'" + text + "'"
    if isinstance(value, uuid.UUID):
        return "'" + str(value).upper() + "'"

    text = str(value)
    ascii_only = tn in ("char", "varchar", "text")
    return _str_literal(text, unicode_prefix=not ascii_only)


def column_ddl(col: dict, db_collation: str | None) -> str:
    """Sinh 1 dong dinh nghia cot."""
    name = q(col["name"])

    if col["is_computed"]:
        persisted = " PERSISTED" if col.get("is_persisted") else ""
        return name + " AS " + str(col["computed_definition"]) + persisted

    parts = [name, render_type(col)]

    # Chi ghi COLLATE khi cot khac collation mac dinh cua database.
    coll = col.get("collation_name")
    if coll and db_collation and coll != db_collation:
        parts.append("COLLATE " + coll)

    if col["is_identity"]:
        seed = int(col["seed_value"] or 1)
        step = int(col["increment_value"] or 1)
        parts.append("IDENTITY(" + str(seed) + "," + str(step) + ")")

    if col.get("is_rowguidcol"):
        parts.append("ROWGUIDCOL")

    parts.append("NULL" if col["is_nullable"] else "NOT NULL")

    if col.get("default_definition"):
        parts.append("CONSTRAINT " + q(col["default_name"]) +
                     " DEFAULT " + str(col["default_definition"]))
    return " ".join(parts)


def insertable_columns(table: dict) -> list:
    """Cot duoc phep xuat hien trong INSERT (bo cot tinh toan va rowversion)."""
    out = []
    for c in table["columns"]:
        if c["is_computed"]:
            continue
        if (c["type_name"] or "").lower() in NON_INSERTABLE:
            continue
        out.append(c)
    return out


# Xuong dong kieu Windows, do CHINH TAY chu khong nho Python doi giup.
# File phai mo bang newline="" - neu khong Python se doi moi '\n' nam trong DU LIEU
# thanh '\r\n', vua sai noi dung vua lam gia tri dai them 1 ky tu va vuot kich thuoc cot.
NL = "\r\n"


class ScriptWriter:
    """Ghi script ra file voi xuong dong co dinh, khong dung tu du lieu."""

    def __init__(self, fh):
        self.fh = fh

    def write(self, text: str = "") -> None:
        self.fh.write(text + NL)

    def go(self) -> None:
        self.fh.write("GO" + NL)

    def rows(self, header: str, values: list) -> None:
        """Ghi 1 cau INSERT nhieu dong: header roi cac bo gia tri, cach nhau bang dau phay."""
        self.fh.write(header + NL)
        self.fh.write(("," + NL).join(values) + ";" + NL)

    def section(self, title: str) -> None:
        bar = "-" * 74
        self.fh.write(NL + bar + NL + "-- " + title + NL + bar + NL)


def find_duplicate_keys(conn, table: dict, key: dict) -> int:
    """Dem so bo khoa bi trung - du lieu cuu ho tu file hong co the co trung."""
    cols = ", ".join(q(c) for c, _ in key["columns"])
    sql = ("SELECT COUNT(*) FROM (SELECT " + cols + " FROM " +
           qn(table["schema"], table["table"]) + " GROUP BY " + cols +
           " HAVING COUNT(*) > 1) AS d")
    try:
        return int(conn.cursor().execute(sql).fetchone()[0])
    except Exception:
        return 0


def find_orphan_rows(conn, fk: dict) -> int:
    """Dem so dong con tro toi khoa cha khong ton tai."""
    on = " AND ".join(
        "c." + q(pc) + " = p." + q(rc)
        for pc, rc in zip(fk["parent_columns"], fk["ref_columns"]))
    not_null = " AND ".join("c." + q(pc) + " IS NOT NULL" for pc in fk["parent_columns"])
    sql = ("SELECT COUNT(*) FROM " + qn(fk["parent_schema"], fk["parent_table"]) + " AS c"
           " LEFT JOIN " + qn(fk["ref_schema"], fk["ref_table"]) + " AS p ON " + on +
           " WHERE " + not_null + " AND p." + q(fk["ref_columns"][0]) + " IS NULL")
    try:
        return int(conn.cursor().execute(sql).fetchone()[0])
    except Exception:
        return 0
