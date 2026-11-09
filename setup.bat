@echo off
cd /d "%~dp0"
echo === Installing dependencies ===
echo Folder: %cd%
echo.
set PY=
python --version >nul 2>&1
if %errorlevel%==0 set PY=python
if "%PY%"=="" (
  py --version >nul 2>&1
  if %errorlevel%==0 set PY=py
)
if "%PY%"=="" goto nopython
echo Using: %PY%
%PY% --version
%PY% -m venv venv
if not exist venv\Scripts\python.exe goto novenv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
if %errorlevel% neq 0 goto piperror
if not exist .env copy .env.example .env
echo.
echo === DONE. Now fill in the .env file ===
goto end

:nopython
echo.
echo ERROR: Python not found. Install Python 3.11+ from python.org
echo and tick "Add python.exe to PATH". Then close this window and run again.
goto end

:novenv
echo.
echo ERROR: could not create folder venv. Check that the zip is fully extracted.
goto end

:piperror
echo.
echo ERROR: could not install libraries. Check your internet connection.

:end
echo.
pause
