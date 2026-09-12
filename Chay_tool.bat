@echo off
chcp 65001 >nul
title mdf2sql - Chuyen file .mdf sang .sql
cd /d "%~dp0"

echo.
echo   mdf2sql - Khoi phuc du lieu SQL Server tu file .mdf
echo   ---------------------------------------------------
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo   [LOI] Chua cai Python tren may.
    echo   Tai tai https://www.python.org/downloads/ roi nho tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

python -c "import pyodbc" >nul 2>&1
if errorlevel 1 (
    echo   Lan dau chay: dang cai thu vien pyodbc...
    python -m pip install --user --quiet pyodbc
    if errorlevel 1 (
        echo   [LOI] Khong cai duoc pyodbc. Kiem tra ket noi mang roi thu lai.
        pause
        exit /b 1
    )
    echo   Da cai xong.
    echo.
)

echo   Dang mo giao dien tren trinh duyet...
echo   Dong cua so nay de tat tool.
echo.
python -m mdf2sql gui

pause
