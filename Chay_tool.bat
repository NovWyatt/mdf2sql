@echo off
chcp 65001 >nul
title mdf2sql - Chuyen file .mdf sang .sql
cd /d "%~dp0"

echo.
echo   mdf2sql - Khoi phuc du lieu SQL Server tu file .mdf
echo   ---------------------------------------------------
echo.

rem pyodbc (ban ghim trong requirements.txt) nam trong .venv rieng cua tool, khong cai vao Python chung cua may.
set "VPY=%~dp0.venv\Scripts\python.exe"
call :moi_truong
if errorlevel 1 exit /b %errorlevel%

echo   Dang mo giao dien tren trinh duyet...
echo   Dong cua so nay de tat tool.
echo.
"%VPY%" -m mdf2sql gui
set "RC=%errorlevel%"
rem Tat binh thuong (Ctrl+C, nut Tat trong Tram Tool) thi dong cua so luon; loi thi dung lai de doc.
if "%RC%"=="0" exit /b 0
echo.
pause
exit /b %RC%

:moi_truong
rem Da co .venv chay duoc pyodbc thi dung luon. .venv hong (Python goc da go, cai do) thi dung lai tu dau.
if exist "%VPY%" (
    "%VPY%" -c "import pyodbc" >nul 2>&1
    if not errorlevel 1 exit /b 0
)
rem Chay thu python that su: "where python" con khop ca loi tat Microsoft Store
rem (WindowsApps\python.exe), loi tat nay mo Store hoac bao loi chu khong chay duoc gi.
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto :thieu_python
echo   Dang tao moi truong rieng .venv va cai pyodbc (can mang, khoang 1 phut)...
if exist ".venv" rmdir /s /q ".venv"
python -m venv .venv
if errorlevel 1 goto :loi_venv
"%VPY%" -m pip install --disable-pip-version-check --quiet --only-binary=:all: -r requirements.txt
if errorlevel 1 goto :loi_pyodbc
echo   Da cai xong.
echo.
exit /b 0

:thieu_python
echo   [LOI] Chua co Python 3.9 tro len (lenh "python" hien chi la loi tat Microsoft Store hoac chua cai).
echo   Cai Python 3.12 tai python.org (nho tick "Add Python to PATH") hoac bang nut Cai trong Tram Tool roi chay lai.
echo.
pause
exit /b 2

:loi_venv
echo   [LOI] Khong tao duoc .venv trong thu muc tool (thieu quyen ghi, hoac Python thieu module venv).
echo.
pause
exit /b 1

:loi_pyodbc
echo   [LOI] Khong cai duoc pyodbc vao .venv. Kiem tra ket noi mang roi chay lai.
echo   Python moi qua chua co ban pyodbc dung san thi cai Python 3.12.
echo.
pause
exit /b 1
