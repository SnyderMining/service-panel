@echo off
cd /d "%~dp0"
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name ServicePanel app.py
echo Built: dist\ServicePanel.exe
