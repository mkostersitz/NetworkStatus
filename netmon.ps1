# NetMon CLI launcher — activates the venv and runs the CLI.
# Usage: .\netmon.ps1 monitor --alerts-config alerts.json
#        .\netmon.ps1 history --hours 24
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
& "$ScriptDir\.venv\Scripts\python.exe" "$ScriptDir\cli.py" @args
