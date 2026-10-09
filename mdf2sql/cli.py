"""Giao dien dong lenh cho mdf2sql."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, convert, dbconn, doctor, server

# Console Windows mac dinh khong phai UTF-8 -> ep lai de tieng Viet co dau khong vo.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return (f"{num:.0f} " if unit == "B" else f"{num:.1f} ") + unit
        num /= 1024
    return str(num)


def _cmd_info(args) -> int:
    path = args.mdf
    if not os.path.isfile(path):
        print("Khong tim thay file:", path)
        return 1
    info = dbconn.mdf_boot_info(path)
    stem = os.path.splitext(path)[0]
    has_ldf = any(os.path.isfile(stem + s) for s in ("_log.ldf", ".ldf"))
    print("File           :", os.path.abspath(path))
    print("Kich thuoc     :", _human(os.path.getsize(path)))
    print("Ten database   :", info["db_name"] or "(khong doc duoc)")
    print("Phien ban file :", info["internal_version"],
          "(" + (info["server_version"] or "khong ro") + ")")
    print("File log .ldf  :", "co" if has_ldf else "khong co")
    print()
    found = dbconn.discover_instances()
    print("SQL Server tren may:", len(found), "instance")
    for i in found:
        print("   -", i.label, "->", i.server, "|", "dang chay" if i.running else "dang tat")
    return 0


def _cmd_convert(args) -> int:
    if not os.path.isfile(args.mdf):
        print("Khong tim thay file:", args.mdf)
        return 1

    out = args.out
    if not out:
        info = dbconn.mdf_boot_info(args.mdf)
        base = info["db_name"] or os.path.splitext(os.path.basename(args.mdf))[0]
        if args.dialect == "mysql":
            base += "_mysql"
        out = os.path.join(os.path.dirname(os.path.abspath(args.mdf)),
                           "mdf2sql_output", base + ".sql")

    opts = convert.Options(
        include_data=not args.schema_only,
        create_database=not args.no_create_db,
        drop_if_exists=not args.no_drop,
        auto_repair=not args.no_repair,
        skip_blobs=args.skip_blobs,
        target_db=args.target_db or "",
        dialect=args.dialect,
        gzip_output=args.gzip,
    )

    last = [""]

    def progress(stage, cur, tot, message):
        if message != last[0]:
            last[0] = message
            print("  " + message, flush=True)

    try:
        rep = convert.convert_mdf(args.mdf, out, server=args.server or "",
                                  opts=opts, progress=progress)
    except dbconn.Mdf2SqlError as exc:
        print("\nLOI:", exc)
        return 2

    print()
    print("Xong sau", f"{rep.seconds:.1f}", "giay")
    print("   File     :", rep.out_path)
    print("   Dung luong:", _human(rep.bytes_written))
    print("   So bang  :", rep.tables)
    print("   So dong  :", f"{rep.rows:,}")
    if rep.rejected:
        print("   Bo qua   :", f"{rep.rejected:,}", "dong loi ->", rep.rejected_path)
    if rep.warnings:
        print("\nGhi chu:")
        for w in rep.warnings:
            print("   -", w)
    return 0


def _cmd_gui(args) -> int:
    try:
        server.serve(port=args.port, open_browser=not args.no_browser)
    except OSError as exc:
        print("LOI: khong mo duoc may chu tren 127.0.0.1:", exc, flush=True)
        return 1
    return 0


def _cmd_doctor(args) -> int:
    return doctor.run(srv=args.server or "", connect=not args.no_connect, as_json=args.json)


def _port(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        n = -1
    if not 0 <= n <= 65535:
        raise argparse.ArgumentTypeError("cong phai tu 0 den 65535 (0 = de he dieu hanh chon)")
    return n


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="mdf2sql",
        description="Chuyen file SQL Server .mdf thanh script .sql import lai duoc.")
    parser.add_argument("--version", action="version", version="mdf2sql " + __version__)
    sub = parser.add_subparsers(dest="command")

    p_gui = sub.add_parser("gui", help="mo giao dien tren trinh duyet")
    p_gui.add_argument("--port", type=_port, default=server.DEFAULT_PORT,
                       help="cong may chu (mac dinh 8760, ban thi tu lui sang cong trong; 0 = de he dieu hanh chon)")
    p_gui.add_argument("--no-browser", action="store_true")
    p_gui.set_defaults(func=_cmd_gui)

    p_doc = sub.add_parser("doctor", help="kiem tra pyodbc, driver ODBC, SQL Server; chi doc, khong sua")
    p_doc.add_argument("--server", help="instance can thu, vd .\\SQLEXPRESS (mac dinh: instance convert se dung)")
    p_doc.add_argument("--no-connect", action="store_true", help="khong thu ket noi SQL Server")
    p_doc.add_argument("--json", action="store_true", help="in ket qua dang JSON")
    p_doc.set_defaults(func=_cmd_doctor)

    p_info = sub.add_parser("info", help="xem thong tin file .mdf, khong can SQL Server")
    p_info.add_argument("mdf")
    p_info.set_defaults(func=_cmd_info)

    p_conv = sub.add_parser("convert", help="chuyen .mdf thanh .sql")
    p_conv.add_argument("mdf", help="duong dan file .mdf")
    p_conv.add_argument("-o", "--out", help="noi luu file .sql")
    p_conv.add_argument("--server", help="instance SQL Server, vd .\\SQLEXPRESS")
    p_conv.add_argument("--target-db", help="ten database khi import lai")
    p_conv.add_argument("--schema-only", action="store_true", help="chi xuat cau truc")
    p_conv.add_argument("--no-create-db", action="store_true",
                        help="khong chen lenh CREATE DATABASE")
    p_conv.add_argument("--no-drop", action="store_true",
                        help="khong chen lenh xoa bang cu")
    p_conv.add_argument("--no-repair", action="store_true",
                        help="khong tu dong sua loi cau truc")
    p_conv.add_argument("--skip-blobs", action="store_true", help="bo qua cot nhi phan lon")
    p_conv.add_argument("--dialect", choices=("mssql", "mysql"), default="mssql",
                        help="mssql = nap lai vao SQL Server (mac dinh), "
                             "mysql = nap vao MySQL/MariaDB/phpMyAdmin")
    p_conv.add_argument("--gzip", action="store_true",
                        help="nen file thanh .sql.gz, phpMyAdmin nap truc tiep duoc")
    p_conv.set_defaults(func=_cmd_convert)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        return _cmd_gui(argparse.Namespace(port=server.DEFAULT_PORT, no_browser=False))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
