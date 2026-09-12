"""Doc toan bo schema cua database da attach tu cac view he thong sys.*."""

from __future__ import annotations

# Kieu du lieu chi nhan do dai trong ngoac, tinh theo ky tu.
_LEN_TYPES = {"char", "varchar", "binary", "varbinary"}
_NLEN_TYPES = {"nchar", "nvarchar"}
# Kieu nhan (precision, scale).
_PREC_SCALE_TYPES = {"decimal", "numeric"}
# Kieu chi nhan scale.
_SCALE_TYPES = {"datetime2", "time", "datetimeoffset"}
# Kieu khong duoc phep INSERT truc tiep.
NON_INSERTABLE = {"timestamp", "rowversion"}
# Kieu nhi phan co the rat nang (anh bien so xe...).
BINARY_TYPES = {"image", "binary", "varbinary"}


def _rows(conn, sql: str, *args):
    cur = conn.cursor()
    cur.execute(sql, *args)
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def render_type(col: dict) -> str:
    """Dung lai chuoi kieu du lieu day du, vd nvarchar(50), decimal(18,2)."""
    name = (col["type_name"] or "").lower()
    max_len = col["max_length"]
    if name in _NLEN_TYPES:
        size = "max" if max_len == -1 else str(int(max_len) // 2)
        return f"{name}({size})"
    if name in _LEN_TYPES:
        size = "max" if max_len == -1 else str(int(max_len))
        return f"{name}({size})"
    if name in _PREC_SCALE_TYPES:
        return f"{name}({int(col['precision'])},{int(col['scale'])})"
    if name in _SCALE_TYPES:
        return f"{name}({int(col['scale'])})"
    if name == "float":
        prec = int(col["precision"] or 53)
        return "float" if prec == 53 else f"float({prec})"
    if name == "xml":
        return "xml"
    return name


def is_binary_blob(col: dict) -> bool:
    """Cot nhi phan kich thuoc lon - thuong la anh chup bien so xe."""
    name = (col["type_name"] or "").lower()
    if name == "image":
        return True
    if name in ("binary", "varbinary"):
        return col["max_length"] == -1 or (col["max_length"] or 0) > 255
    return False


def database_info(conn) -> dict:
    row = _rows(conn, """
        SELECT DB_NAME() AS name, d.collation_name, d.compatibility_level,
               d.recovery_model_desc, d.state_desc
        FROM sys.databases d WHERE d.database_id = DB_ID()""")
    return row[0] if row else {"name": "", "collation_name": None}


def schemas(conn) -> list:
    """Cac schema nguoi dung tao them (ngoai dbo)."""
    return [r["name"] for r in _rows(conn, """
        SELECT s.name FROM sys.schemas s
        WHERE s.name NOT IN ('dbo','guest','sys','INFORMATION_SCHEMA')
          AND s.name NOT LIKE 'db[_]%'
        ORDER BY s.name""")]


def tables(conn) -> list:
    """Danh sach bang kem so dong uoc tinh."""
    return _rows(conn, """
        SELECT s.name AS [schema], t.name AS [table], t.object_id,
               ISNULL((SELECT SUM(p.rows) FROM sys.partitions p
                       WHERE p.object_id = t.object_id AND p.index_id IN (0,1)), 0) AS row_count
        FROM sys.tables t
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        WHERE t.is_ms_shipped = 0 AND t.name <> 'sysdiagrams'
        ORDER BY s.name, t.name""")


def columns(conn) -> dict:
    """Cot cua tung bang, gom theo object_id."""
    rows = _rows(conn, """
        SELECT c.object_id, c.column_id, c.name,
               ty.name AS type_name, ty.is_user_defined,
               c.max_length, c.precision, c.scale, c.is_nullable,
               c.is_identity, c.is_computed, c.is_rowguidcol, c.collation_name,
               cc.definition AS computed_definition, cc.is_persisted,
               ic.seed_value, ic.increment_value,
               dc.name AS default_name, dc.definition AS default_definition
        FROM sys.columns c
        JOIN sys.tables t ON t.object_id = c.object_id
        JOIN sys.types ty ON ty.user_type_id = c.user_type_id
        LEFT JOIN sys.computed_columns cc
               ON cc.object_id = c.object_id AND cc.column_id = c.column_id
        LEFT JOIN sys.identity_columns ic
               ON ic.object_id = c.object_id AND ic.column_id = c.column_id
        LEFT JOIN sys.default_constraints dc
               ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
        WHERE t.is_ms_shipped = 0
        ORDER BY c.object_id, c.column_id""")
    grouped: dict = {}
    for r in rows:
        grouped.setdefault(r["object_id"], []).append(r)
    return grouped


def key_constraints(conn) -> dict:
    """Khoa chinh va rang buoc UNIQUE."""
    rows = _rows(conn, """
        SELECT kc.parent_object_id AS object_id, kc.name, kc.type,
               i.index_id, i.type_desc, i.is_padded, i.fill_factor,
               c.name AS column_name, ic.key_ordinal, ic.is_descending_key
        FROM sys.key_constraints kc
        JOIN sys.indexes i ON i.object_id = kc.parent_object_id AND i.index_id = kc.unique_index_id
        JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        JOIN sys.tables t ON t.object_id = kc.parent_object_id
        WHERE t.is_ms_shipped = 0
        ORDER BY kc.parent_object_id, kc.name, ic.key_ordinal""")
    grouped: dict = {}
    for r in rows:
        key = (r["object_id"], r["name"])
        entry = grouped.setdefault(key, {
            "object_id": r["object_id"], "name": r["name"], "type": r["type"].strip(),
            "type_desc": r["type_desc"], "columns": [],
        })
        entry["columns"].append((r["column_name"], bool(r["is_descending_key"])))
    by_table: dict = {}
    for entry in grouped.values():
        by_table.setdefault(entry["object_id"], []).append(entry)
    return by_table


def indexes(conn) -> dict:
    """Index thuong (khong phai PK/UNIQUE constraint, khong phai heap)."""
    rows = _rows(conn, """
        SELECT i.object_id, i.name, i.index_id, i.is_unique, i.type_desc,
               i.has_filter, i.filter_definition,
               c.name AS column_name, ic.key_ordinal, ic.is_descending_key,
               ic.is_included_column
        FROM sys.indexes i
        JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        JOIN sys.tables t ON t.object_id = i.object_id
        WHERE t.is_ms_shipped = 0 AND i.is_primary_key = 0 AND i.is_unique_constraint = 0
          AND i.type IN (1, 2) AND i.name IS NOT NULL
        ORDER BY i.object_id, i.name, ic.is_included_column, ic.key_ordinal""")
    grouped: dict = {}
    for r in rows:
        key = (r["object_id"], r["name"])
        entry = grouped.setdefault(key, {
            "object_id": r["object_id"], "name": r["name"],
            "is_unique": bool(r["is_unique"]), "type_desc": r["type_desc"],
            "filter": r["filter_definition"] if r["has_filter"] else None,
            "key_columns": [], "included_columns": [],
        })
        if r["is_included_column"]:
            entry["included_columns"].append(r["column_name"])
        else:
            entry["key_columns"].append((r["column_name"], bool(r["is_descending_key"])))
    by_table: dict = {}
    for entry in grouped.values():
        by_table.setdefault(entry["object_id"], []).append(entry)
    return by_table


def foreign_keys(conn) -> list:
    rows = _rows(conn, """
        SELECT fk.object_id, fk.name,
               ps.name AS parent_schema, pt.name AS parent_table,
               rs.name AS ref_schema, rt.name AS ref_table,
               fk.delete_referential_action_desc AS on_delete,
               fk.update_referential_action_desc AS on_update,
               fk.is_disabled, fk.is_not_trusted,
               pc.name AS parent_column, rc.name AS ref_column, fkc.constraint_column_id
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
        JOIN sys.tables pt ON pt.object_id = fk.parent_object_id
        JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
        JOIN sys.tables rt ON rt.object_id = fk.referenced_object_id
        JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
        JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id
                           AND pc.column_id = fkc.parent_column_id
        JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id
                           AND rc.column_id = fkc.referenced_column_id
        ORDER BY fk.object_id, fkc.constraint_column_id""")
    grouped: dict = {}
    for r in rows:
        entry = grouped.setdefault(r["object_id"], {
            "name": r["name"], "parent_schema": r["parent_schema"],
            "parent_table": r["parent_table"], "ref_schema": r["ref_schema"],
            "ref_table": r["ref_table"], "on_delete": r["on_delete"],
            "on_update": r["on_update"], "is_disabled": bool(r["is_disabled"]),
            "is_not_trusted": bool(r["is_not_trusted"]),
            "parent_columns": [], "ref_columns": [],
        })
        entry["parent_columns"].append(r["parent_column"])
        entry["ref_columns"].append(r["ref_column"])
    return list(grouped.values())


def check_constraints(conn) -> list:
    return _rows(conn, """
        SELECT cc.name, s.name AS [schema], t.name AS [table],
               cc.definition, cc.is_disabled, cc.is_not_trusted
        FROM sys.check_constraints cc
        JOIN sys.tables t ON t.object_id = cc.parent_object_id
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        WHERE t.is_ms_shipped = 0
        ORDER BY s.name, t.name, cc.name""")


def modules(conn) -> list:
    """View, stored procedure, function, trigger - kem ma nguon goc."""
    return _rows(conn, """
        SELECT o.type_desc, s.name AS [schema], o.name, m.definition,
               ISNULL(OBJECT_SCHEMA_NAME(tr.parent_id), '') AS trigger_parent_schema,
               ISNULL(OBJECT_NAME(tr.parent_id), '') AS trigger_parent_table
        FROM sys.sql_modules m
        JOIN sys.objects o ON o.object_id = m.object_id
        JOIN sys.schemas s ON s.schema_id = o.schema_id
        LEFT JOIN sys.triggers tr ON tr.object_id = o.object_id
        WHERE o.is_ms_shipped = 0
          AND o.type IN ('V','P','FN','IF','TF','TR')
        ORDER BY CASE o.type WHEN 'V' THEN 1 WHEN 'FN' THEN 2 WHEN 'IF' THEN 2
                             WHEN 'TF' THEN 2 WHEN 'P' THEN 3 ELSE 4 END, s.name, o.name""")


def user_types(conn) -> list:
    """Kieu du lieu do nguoi dung tu dinh nghia (alias type)."""
    return _rows(conn, """
        SELECT s.name AS [schema], t.name, bt.name AS base_type,
               t.max_length, t.precision, t.scale, t.is_nullable
        FROM sys.types t
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        JOIN sys.types bt ON bt.user_type_id = t.system_type_id AND bt.is_user_defined = 0
        WHERE t.is_user_defined = 1 AND t.is_table_type = 0
        ORDER BY s.name, t.name""")


def read_all(conn) -> dict:
    """Doc toan bo schema, tra ve 1 dict duy nhat cho buoc sinh script."""
    tabs = tables(conn)
    cols = columns(conn)
    keys = key_constraints(conn)
    idxs = indexes(conn)
    for t in tabs:
        t["columns"] = cols.get(t["object_id"], [])
        t["keys"] = keys.get(t["object_id"], [])
        t["indexes"] = idxs.get(t["object_id"], [])
    return {
        "database": database_info(conn),
        "schemas": schemas(conn),
        "user_types": user_types(conn),
        "tables": tabs,
        "foreign_keys": foreign_keys(conn),
        "check_constraints": check_constraints(conn),
        "modules": modules(conn),
    }
