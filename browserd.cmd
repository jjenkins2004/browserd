@echo off
rem Start, stop or restart the browser MCP server on Windows, from cmd or PowerShell: browserd start, browserd stop or
rem browserd restart, as ./start, ./stop and ./restart do elsewhere. Works from any directory. Not start.cmd: start is a
rem command of cmd's own, and an alias of PowerShell's. BROWSERD_PYTHON picks the Python; py -3 otherwise.
setlocal
set "PY=%BROWSERD_PYTHON%"
if not defined PY (
  where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
)
pushd "%~dp0"
%PY% -m browser.service %*
set "CODE=%ERRORLEVEL%"
popd
exit /b %CODE%
