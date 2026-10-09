@echo off
chcp 65001 >nul
title mdf2sql - dong lenh
cd /d "%~dp0"
rem Chay "python -m mdf2sql <lenh> ..." (info, convert) va giu cua so de doc ket qua.
rem Tram Tool goi file nay cho cac nut "Xem thong tin file .mdf" va "Chuyen doi bang dong lenh".

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto :thieu_python
rem "info" chi doc phan dau file .mdf: khong can pyodbc, khong can SQL Server.
rem "doctor" phai bao thieu pyodbc chu khong tu cai.
if /i "%~1"=="info" goto :chay
if /i "%~1"=="doctor" goto :chay
python -c "import pyodbc" >nul 2>&1
if errorlevel 1 (
    echo   Lan dau chay: dang cai thu vien pyodbc...
    python -m pip install --user --quiet -r requirements.txt
    if errorlevel 1 goto :loi_pyodbc
)

:chay
python -u -m mdf2sql %*
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%

:thieu_python
echo   [LOI] Chua co Python 3.9 tro len (lenh "python" hien chi la loi tat Microsoft Store hoac chua cai).
echo   Cai Python 3.12 tai python.org hoac bang nut Cai trong Tram Tool roi chay lai.
pause
exit /b 2

:loi_pyodbc
echo   [LOI] Khong cai duoc pyodbc. Kiem tra ket noi mang roi thu lai.
pause
exit /b 1
