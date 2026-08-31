# NetMon web server launcher.
# Usage: .\serve.ps1 [--host 0.0.0.0] [--port 8080]
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Host_   = if ($env:NETMON_HOST)  { $env:NETMON_HOST }  else { "0.0.0.0" }
$Port    = if ($env:NETMON_PORT)  { $env:NETMON_PORT }  else { "8080" }
& "$ScriptDir\.venv\Scripts\uvicorn.exe" web.app:app `
    --host $Host_ `
    --port $Port `
    --app-dir "$ScriptDir" `
    @args
