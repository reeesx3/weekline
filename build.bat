@echo off
setlocal
cd /d "%~dp0"
if not exist ".build-venv\Scripts\python.exe" (
    python -m venv .build-venv || exit /b 1
)
set "PY=.build-venv\Scripts\python.exe"
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt "pyinstaller>=6.8,<7" || exit /b 1
"%PY%" -m pip check || exit /b 1
"%PY%" -m compileall -q widget.py translations.py schedule_diff.py || exit /b 1
set "PATH=%CD%\.build-venv\Scripts;%SystemRoot%\System32;%SystemRoot%"
"%PY%" -m PyInstaller --noconfirm --clean --onefile --noconsole --noupx ^
    --name ScheduleWidget --icon docs\images\app.ico ^
    --add-data "schedule.example.json;." ^
    --hidden-import translations --hidden-import schedule_diff ^
    --exclude-module tkinter --exclude-module unittest --exclude-module pydoc ^
    widget.py || exit /b 1
echo Built dist\ScheduleWidget.exe
