@echo off
chcp 65001 >nul
title mdf2sql - dong lenh
cd /d "%~dp0"
rem Chay "python -m mdf2sql <lenh> ..." (info, convert, doctor) va giu cua so de doc ket qua.
rem Tram Tool goi file nay cho cac nut "Xem thong tin file .mdf", "Chuyen doi bang dong lenh", "Kiem tra may".
rem pyodbc nam trong .venv rieng cua tool (xem Chay_tool.bat).

set "VPY=%~dp0.venv\Scripts\python.exe"
set "PY="
if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=%VPY%"
)
rem "cai-dat [--lam-lai]": chi tao hoac sua .venv (nut "Cai hoac sua moi truong chay" cua Tram Tool).
if /i "%~1"=="cai-dat" goto :cai_dat
rem "info" chi doc phan dau file .mdf: khong can pyodbc, khong can SQL Server.
rem "doctor" phai bao thieu pyodbc chu khong tu cai: chua co .venv thi chay bang Python chung.
if /i "%~1"=="info" goto :khong_can_venv
if /i "%~1"=="doctor" goto :khong_can_venv
call :moi_truong
if errorlevel 1 exit /b %errorlevel%
set "PY=%VPY%"
goto :chay

:khong_can_venv
if defined PY goto :chay
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto :thieu_python
set "PY=python"

:chay
"%PY%" -u -m mdf2sql %*
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%

:cai_dat
if /i "%~2"=="--lam-lai" set "LAM_LAI=1"
call :moi_truong
if errorlevel 1 exit /b %errorlevel%
"%VPY%" -c "import sys, pyodbc; print('  Python', sys.version.split()[0], '- pyodbc', pyodbc.version, '- trong', sys.prefix)"
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" echo   XONG. Moi truong chay da san sang.
pause
exit /b %RC%

:moi_truong
if not defined LAM_LAI if exist "%VPY%" (
    "%VPY%" -c "import pyodbc" >nul 2>&1
    if not errorlevel 1 exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto :thieu_python
echo   Dang tao moi truong rieng .venv va cai pyodbc (can mang, khoang 1 phut)...
if exist ".venv" rmdir /s /q ".venv"
python -m venv .venv
if errorlevel 1 goto :loi_venv
"%VPY%" -m pip install --disable-pip-version-check --quiet --only-binary=:all: -r requirements.txt
if errorlevel 1 goto :loi_pyodbc
exit /b 0

:thieu_python
echo   [LOI] Chua co Python 3.9 tro len (lenh "python" hien chi la loi tat Microsoft Store hoac chua cai).
echo   Cai Python 3.12 tai python.org hoac bang nut Cai trong Tram Tool roi chay lai.
pause
exit /b 2

:loi_venv
echo   [LOI] Khong tao duoc .venv trong thu muc tool (thieu quyen ghi, hoac Python thieu module venv).
pause
exit /b 1

:loi_pyodbc
echo   [LOI] Khong cai duoc pyodbc vao .venv. Kiem tra ket noi mang roi chay lai.
echo   Python moi qua chua co ban pyodbc dung san thi cai Python 3.12.
pause
exit /b 1
