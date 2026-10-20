@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 이상을 설치한 뒤 다시 실행해 주세요. https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe run.py
pause
exit /b
:failed
echo 설치 또는 실행에 실패했습니다. 위 오류를 확인해 주세요.
pause
exit /b 1
