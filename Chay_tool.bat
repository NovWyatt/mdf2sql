@echo off
chcp 65001 >nul
title mdf2sql - Chuyen file .mdf sang .sql
cd /d "%~dp0"

echo.
echo   mdf2sql - Khoi phuc du lieu SQL Server tu file .mdf
echo   ---------------------------------------------------
echo.

rem Chay thu python that su: "where python" con khop ca loi tat Microsoft Store
rem (WindowsApps\python.exe), loi tat nay mo Store hoac bao loi chu khong chay duoc gi.
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto :thieu_python

python -c "import pyodbc" >nul 2>&1
if errorlevel 1 (
    echo   Lan dau chay: dang cai thu vien pyodbc...
    python -m pip install --user --quiet -r requirements.txt
    if errorlevel 1 goto :loi_pyodbc
    echo   Da cai xong.
    echo.
)

echo   Dang mo giao dien tren trinh duyet...
echo   Dong cua so nay de tat tool.
echo.
python -m mdf2sql gui
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%

:thieu_python
echo   [LOI] Chua co Python 3.9 tro len (lenh "python" hien chi la loi tat Microsoft Store hoac chua cai).
echo   Cai Python 3.12 tai python.org (nho tick "Add Python to PATH") hoac bang nut Cai trong Tram Tool roi chay lai.
echo.
pause
exit /b 2

:loi_pyodbc
echo   [LOI] Khong cai duoc pyodbc. Kiem tra ket noi mang roi thu lai.
echo.
pause
exit /b 1
