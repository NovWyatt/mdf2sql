"""pyodbc gia cho bai thu: khong noi toi driver ODBC hay SQL Server nao. Bai thu thay drivers/connect khi can."""

version = "0-gia"


class Error(Exception):
    pass


def drivers():
    return ["ODBC Driver 18 for SQL Server"]


def connect(*args, **kwargs):
    raise Error("pyodbc gia: khong co SQL Server")
