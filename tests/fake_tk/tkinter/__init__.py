"""tkinter gia cho bai thu hop thoai chon file (mdf2sql.pick): khong mo cua so nao.

Bai thu dat thu muc tests/fake_tk vao PYTHONPATH cua tien trinh con nen goi nay che tkinter that.
  FAKE_TK_MISSING=1   gia lap Python khong kem tkinter (ImportError)
  FAKE_TK_LOG=<file>  ghi lai cac lenh Tk da goi (moi dong mot JSON) de bai thu kiem
Ket qua hop thoai: xem filedialog.py.
"""

import json
import os
import threading

if os.environ.get("FAKE_TK_MISSING") == "1":
    raise ImportError("No module named '_tkinter' (gia lap)")


def _log(event, **fields):
    path = os.environ.get("FAKE_TK_LOG")
    if path:
        fields.update(event=event, pid=os.getpid(),
                      main_thread=threading.current_thread() is threading.main_thread())
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fields, ensure_ascii=False) + "\n")


class Tk:
    def __init__(self):
        _log("Tk")

    def withdraw(self):
        _log("withdraw")

    def attributes(self, *args):
        _log("attributes", args=list(args))

    def destroy(self):
        _log("destroy")
