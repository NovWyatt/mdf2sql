"""mdf2sql - Chuyen doi file SQL Server .mdf thanh script .sql import duoc.

Package nay gom cac module:
    dbconn     : do tim instance SQL Server tren may va mo ket noi
    attacher   : copy .mdf ra vung lam viec roi attach vao SQL Server
    introspect : doc schema (bang, cot, khoa, index, view, proc...) tu sys.*
    emit       : sinh script T-SQL (DDL + INSERT) import lai khong loi
    convert    : dieu phoi toan bo quy trinh mdf -> sql
    server     : web GUI chay tren localhost
    pick       : hop thoai chon file (Tk) chay trong tien trinh rieng
    doctor     : kiem tra moi truong (pyodbc, driver ODBC, SQL Server)
"""

__version__ = "1.3.0"
