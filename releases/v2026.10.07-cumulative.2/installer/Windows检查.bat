@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto badpath
if not exist "logs" mkdir "logs"
if not exist "logs" goto badlogs
echo [1/2] Checking bundled Python...
if not exist "runtime\windows-x64\python.exe" goto missing
"runtime\windows-x64\python.exe" -I -S -B -X utf8 -c "import sys; print(sys.version)" >"logs\python-startup.log" 2>&1
set "PATCH_EXIT=%ERRORLEVEL%"
type "logs\python-startup.log"
if not "%PATCH_EXIT%"=="0" goto failed
echo [2/2] Starting installer...
"runtime\windows-x64\python.exe" -I -S -B -X utf8 "run.py" --check %* 2>"logs\python-stderr.log"
set "PATCH_EXIT=%ERRORLEVEL%"
type "logs\python-stderr.log"
echo Exit code: %PATCH_EXIT%
echo Diagnostic logs are in the logs folder.
pause
popd
exit /b %PATCH_EXIT%
:missing
echo ERROR: Bundled Python is missing. Extract the complete original package first.
echo Do not run this hotfix as a standalone installer.
set "PATCH_EXIT=1"
goto failed
:failed
echo ERROR: Python or installer could not start. See logs and keep this window open.
pause
popd
exit /b %PATCH_EXIT%
:badlogs
echo ERROR: Cannot create logs. Extract the package into a writable local folder.
pause
popd
exit /b 1
:badpath
echo ERROR: Cannot open the installer folder. Extract the ZIP to a local folder first.
pause
exit /b 1
