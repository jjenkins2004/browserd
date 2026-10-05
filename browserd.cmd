@echo off
rem The browserd command (browser/cli/service.py) on Windows, from cmd or PowerShell, as the browserd script is
rem elsewhere. Works from any directory; install.ps1 puts a shim for it on the PATH. Not start.cmd: start is a command of
rem cmd's own, and an alias of PowerShell's. BROWSERD_PYTHON picks the Python; py -3 otherwise.
setlocal
set "PY=%BROWSERD_PYTHON%"
if not defined PY (
  where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
)
pushd "%~dp0"
%PY% -m browser.cli.service %*
set "CODE=%ERRORLEVEL%"
popd
exit /b %CODE%
