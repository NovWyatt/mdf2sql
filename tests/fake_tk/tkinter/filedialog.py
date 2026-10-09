"""filedialog gia: tra ket qua theo bien moi truong, khong mo cua so.

  FAKE_TK_OPEN=<duong dan>   askopenfilename tra ve chuoi nay ("" = bam Huy)
  FAKE_TK_SAVE=echo          asksaveasfilename tra ve initialdir\\initialfile (kiem tham so)
  FAKE_TK_SAVE=<duong dan>   asksaveasfilename tra ve chuoi nay
  FAKE_TK_RAISE=1            hop thoai nem loi Tcl gia
"""

import os

from . import _log


class TclError(Exception):
    pass


def _maybe_raise():
    if os.environ.get("FAKE_TK_RAISE") == "1":
        raise TclError("loi Tcl gia lap")


def askopenfilename(**kwargs):
    _log("askopenfilename", title=kwargs.get("title", ""))
    _maybe_raise()
    return os.environ.get("FAKE_TK_OPEN", "")


def asksaveasfilename(**kwargs):
    _log("asksaveasfilename", title=kwargs.get("title", ""), initialdir=kwargs.get("initialdir", ""),
         initialfile=kwargs.get("initialfile", ""), defaultextension=kwargs.get("defaultextension", ""))
    _maybe_raise()
    mode = os.environ.get("FAKE_TK_SAVE", "")
    if mode == "echo":
        return os.path.join(kwargs.get("initialdir", ""), kwargs.get("initialfile", ""))
    return mode
