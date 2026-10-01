@echo off
setlocal
pushd "%~dp0"
if not exist "runtime\windows-x64\python.exe" goto missing
"runtime\windows-x64\python.exe" -I -S -B -X utf8 "run.py" %*
set "UC_EXIT=%ERRORLEVEL%"
pause
popd
exit /b %UC_EXIT%
:missing
echo Bundled Python is missing. Extract the entire ZIP and try again.
pause
popd
exit /b 1
