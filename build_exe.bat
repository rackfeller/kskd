@echo off
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo ERROR: venv not found. Run setup.bat first.
  pause
  exit /b 1
)
echo === Installing PyInstaller ===
venv\Scripts\python.exe -m pip install pyinstaller
if %errorlevel% neq 0 goto err
echo === Building Userbot.exe (a few minutes) ===
venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name Userbot --icon icon.ico --collect-all openai --collect-all telethon --hidden-import pytz gui.py
if %errorlevel% neq 0 goto err
if not exist release mkdir release
copy /y dist\Userbot.exe release\Userbot.exe >nul
if exist .env copy /y .env release\.env >nul
if not exist release\.env copy /y .env.example release\.env >nul
copy /y templates.txt release\templates.txt >nul
echo.
echo === DONE ===
echo Folder "release": Userbot.exe + .env + templates.txt
echo Keep these 3 files together. Double-click Userbot.exe to run.
goto end
:err
echo.
echo ERROR: build failed, see messages above.
:end
echo.
pause
