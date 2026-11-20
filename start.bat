@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  VoiceDub Studio
echo  http://127.0.0.1:8000
echo ============================================
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
