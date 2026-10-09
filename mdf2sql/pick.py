"""Hop thoai chon file .mdf / luu file .sql, chay trong tien trinh rieng.

Tk chi an toan khi moi lenh Tk chay tren cung mot luong. May chu giao dien phuc vu moi yeu cau
tren mot luong khac nhau; tao Tk o do doi khi lam sap ca tien trinh (vd "Tcl_AsyncDelete: async
handler deleted by the wrong thread" khi doi tuong Tk bi thu hoi o luong khac). Chay hop thoai
trong tien trinh con thi luong chinh cua no la luong Tk, va co loi cung chi mat lan chon do.

    python -m mdf2sql.pick mdf|sql [duong_dan_hien_tai]

In ra mot dong JSON chi gom ky tu ASCII: {"path": "..."} (chuoi rong khi bam Huy), hoac
{"path": "", "error": "..."} khi khong mo duoc hop thoai.
"""

from __future__ import annotations

import json
import os
import sys


def ask(kind: str, current: str = "") -> str:
    """Mo hop thoai chon file cua Windows; tra ve duong dan da chon hoac chuoi rong."""
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "mdf":
            path = filedialog.askopenfilename(
                title="Chọn file .mdf cần chuyển đổi",
                filetypes=[("SQL Server database", "*.mdf"), ("Tất cả file", "*.*")])
        else:
            initial = os.path.dirname(current) if current else ""
            name = os.path.basename(current) or "database.sql"
            path = filedialog.asksaveasfilename(
                title="Lưu file .sql", defaultextension=".sql",
                initialfile=name, initialdir=initial,
                filetypes=[("Script SQL", "*.sql"), ("Tất cả file", "*.*")])
        return path or ""
    finally:
        root.destroy()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    kind = "sql" if argv and argv[0] == "sql" else "mdf"
    current = argv[1] if len(argv) > 1 else ""
    result = {"path": ""}
    try:
        result["path"] = ask(kind, current)
    except ImportError:
        result["error"] = ("Python trên máy này thiếu tkinter nên không mở được hộp thoại chọn file. "
                           "Dán đường dẫn vào ô hoặc cài lại Python có tick tcl/tk.")
    except Exception as exc:                      # Tcl/Tk loi: bao ve may chu thay vi im lang
        result["error"] = "Không mở được hộp thoại chọn file: " + type(exc).__name__ + ": " + str(exc)
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
