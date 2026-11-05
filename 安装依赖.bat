@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -m pip install -r requirements.txt
) else (
    python -m pip install -r requirements.txt
)
echo.
echo If installation succeeded, run the analyzer launcher.
pause
