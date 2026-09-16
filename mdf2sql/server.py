"""May chu web cuc bo cho giao dien mdf2sql.

Chi lang nghe tren 127.0.0.1 va doi mot ma thong hanh ngau nhien sinh luc khoi dong,
nen trang web khac tren may khong goi duoc API nay.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import subprocess
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import attacher, convert, dbconn

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TOKEN = secrets.token_urlsafe(24)

# Ti le phan tram uoc tinh cho tung giai doan, de thanh tien trinh chay muot.
STAGE_PERCENT = {
    "connect": 3, "probe": 6, "copy": 10, "attach": 18,
    "check": 34, "schema": 44, "emit": 46, "keys": 92, "cleanup": 97,
}

_jobs: dict = {}
_jobs_lock = threading.Lock()
_dialog_lock = threading.Lock()


# ------------------------------------------------------------------ cong viec

def _new_job() -> str:
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"percent": 0, "message": "Đang chuẩn bị", "stage": "",
                         "done": False, "error": "", "report": None}
    return job_id


def _update(job_id: str, **fields) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


def _run_job(job_id: str, payload: dict) -> None:
    """Chay convert trong luong rieng, cap nhat tien do vao _jobs."""

    def progress(stage, cur, tot, message):
        pct = STAGE_PERCENT.get(stage, 50)
        if stage == "data" and tot:
            pct = 46 + int(44 * cur / tot)      # giai doan do du lieu chiem 46% -> 90%
        _update(job_id, percent=min(pct, 99), message=message, stage=stage)

    try:
        opts = convert.Options(
            include_data=bool(payload.get("include_data", True)),
            create_database=bool(payload.get("create_database", True)),
            drop_if_exists=bool(payload.get("drop_if_exists", True)),
            auto_repair=bool(payload.get("auto_repair", True)),
            skip_blobs=bool(payload.get("skip_blobs", False)),
            target_db=(payload.get("target_db") or "").strip(),
            dialect=("mysql" if payload.get("dialect") == "mysql" else "mssql"),
            gzip_output=bool(payload.get("gzip_output", False)),
        )
        rep = convert.convert_mdf(payload["path"], payload["out"], opts=opts, progress=progress)
        _update(job_id, percent=100, message="Hoàn tất", done=True, report={
            "ok": rep.ok, "out_path": rep.out_path, "db_name": rep.db_name,
            "strategy": rep.strategy, "repaired": rep.repaired, "tables": rep.tables,
            "rows": rep.rows, "rejected": rep.rejected, "rejected_path": rep.rejected_path,
            "bytes_written": rep.bytes_written, "seconds": round(rep.seconds, 2),
            "warnings": rep.warnings, "details": rep.details, "table_stats": rep.table_stats,
        })
    except dbconn.Mdf2SqlError as exc:
        _update(job_id, done=True, error=str(exc))
    except Exception as exc:                      # pragma: no cover
        traceback.print_exc()
        _update(job_id, done=True, error=type(exc).__name__ + ": " + str(exc))


# ------------------------------------------------------------------ hop thoai

def _pick_file(kind: str, current: str) -> str:
    """Mo hop thoai chon file cua Windows."""
    with _dialog_lock:
        try:
            import tkinter
            from tkinter import filedialog
        except ImportError:
            return ""
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


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "mdf2sql"

    def log_message(self, fmt, *args):            # bot bot log ra console
        return

    # ---- tien ich ----
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _authorized(self) -> bool:
        """Chan trang web khac tren may goi vao API nay."""
        if self.headers.get("X-Token") == TOKEN:
            return True
        query = parse_qs(urlparse(self.path).query)
        return query.get("k", [""])[0] == TOKEN

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    # ---- GET ----
    def do_GET(self):
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            return self._file("index.html")
        if route in ("/app.css", "/app.js"):
            return self._file(route.lstrip("/"))
        if route == "/api/instances":
            return self._instances()
        if route == "/api/progress":
            return self._progress()
        self._json({"error": "Không có đường dẫn này"}, 404)

    def _file(self, name: str) -> None:
        path = os.path.join(WEB_DIR, name)
        if not os.path.isfile(path):
            return self._json({"error": "Thiếu file giao diện " + name}, 404)
        with open(path, "rb") as fh:
            data = fh.read()
        ctype = mimetypes.guess_type(path)[0] or "text/plain"
        self._send(200, data, ctype + "; charset=utf-8")

    def _instances(self) -> None:
        if not self._authorized():
            return self._json({"error": "Từ chối truy cập"}, 403)
        try:
            dbconn.ensure_localdb_started()
            found = [i for i in dbconn.discover_instances() if i.running]
            return self._json({"instances": [
                {"server": i.server, "label": i.label, "kind": i.kind} for i in found]})
        except dbconn.Mdf2SqlError as exc:
            return self._json({"error": str(exc)}, 500)

    def _progress(self) -> None:
        if not self._authorized():
            return self._json({"error": "Từ chối truy cập"}, 403)
        job_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job is None:
            return self._json({"error": "Không tìm thấy phiên chạy"}, 404)
        self._json(job)

    # ---- POST ----
    def do_POST(self):
        if not self._authorized():
            return self._json({"error": "Từ chối truy cập"}, 403)
        route = urlparse(self.path).path
        payload = self._body()
        try:
            if route == "/api/inspect":
                return self._inspect(payload)
            if route == "/api/pick":
                return self._pick(payload)
            if route == "/api/convert":
                return self._convert(payload)
            if route == "/api/reveal":
                return self._reveal(payload)
        except dbconn.Mdf2SqlError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:                  # pragma: no cover
            traceback.print_exc()
            return self._json({"error": type(exc).__name__ + ": " + str(exc)}, 500)
        self._json({"error": "Không có đường dẫn này"}, 404)

    def _inspect(self, payload: dict) -> None:
        path = (payload.get("path") or "").strip().strip('"')
        if not path:
            return self._json({"error": "Chưa chọn file"}, 400)
        if not os.path.isfile(path):
            return self._json({"error": "Không tìm thấy file: " + path}, 400)
        if not path.lower().endswith(".mdf"):
            return self._json({"error": "Đây không phải file .mdf"}, 400)

        info = dbconn.mdf_boot_info(path)
        stem = os.path.splitext(path)[0]
        has_ldf = any(os.path.isfile(stem + suffix) for suffix in ("_log.ldf", ".ldf"))
        out_dir = os.path.join(os.path.dirname(path), "mdf2sql_output")
        base = info.get("db_name") or os.path.splitext(os.path.basename(path))[0]
        self._json({
            "path": path,
            "name": os.path.basename(path),
            "size": os.path.getsize(path),
            "db_name": info.get("db_name", ""),
            "internal_version": info.get("internal_version", 0),
            "server_version": info.get("server_version", ""),
            "has_ldf": has_ldf,
            "suggest_out": os.path.join(out_dir, base + ".sql"),
        })

    def _pick(self, payload: dict) -> None:
        path = _pick_file(payload.get("kind") or "mdf", payload.get("current") or "")
        self._json({"path": os.path.normpath(path) if path else ""})

    def _convert(self, payload: dict) -> None:
        path = (payload.get("path") or "").strip().strip('"')
        out = (payload.get("out") or "").strip().strip('"')
        if not os.path.isfile(path):
            return self._json({"error": "Không tìm thấy file .mdf"}, 400)
        if not out:
            return self._json({"error": "Chưa chọn nơi lưu file .sql"}, 400)
        job_id = _new_job()
        threading.Thread(target=_run_job, args=(job_id, dict(payload, path=path, out=out)),
                         daemon=True).start()
        self._json({"job_id": job_id})

    def _reveal(self, payload: dict) -> None:
        path = payload.get("path") or ""
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif os.path.isdir(path):
            subprocess.Popen(["explorer", os.path.normpath(path)])
        self._json({"ok": True})


def serve(port: int = 8760, open_browser: bool = True) -> None:
    """Khoi dong may chu va mo trinh duyet."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:" + str(port) + "/?k=" + TOKEN
    print("mdf2sql dang chay tai:")
    print("   " + url)
    print("Dong cua so nay de tat tool.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDa tat.")
    finally:
        httpd.server_close()
