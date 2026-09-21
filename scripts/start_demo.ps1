<#!
.SYNOPSIS
Starts the Synthetic User Lab operator console and its controlled Flowboard demo.

.DESCRIPTION
This is a local demo launcher, not a production process manager. It expects a
prepared virtual environment and reads secrets only from the local .env file.
#>
[CmdletBinding()]
param(
    [int]$LabPort = 8000,
    [int]$DemoPort = 8001
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Virtual environment missing. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[test,browser,postgres,model]"'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination (Join-Path $projectRoot '.env')
    Write-Warning 'Created .env from .env.example. Configure the model/database values, then run this command again.'
    exit 1
}

Start-Process -FilePath $pythonExe -ArgumentList '-m','uvicorn','synthetic_lab.demo.app:create_demo_app','--factory','--host','127.0.0.1','--port',$DemoPort -WorkingDirectory $projectRoot -WindowStyle Hidden
Start-Process -FilePath $pythonExe -ArgumentList '-m','uvicorn','synthetic_lab.api.app:app','--host','127.0.0.1','--port',$LabPort -WorkingDirectory $projectRoot -WindowStyle Hidden

Write-Host "Operator console: http://127.0.0.1:$LabPort/dashboard"
Write-Host "Controlled Flowboard target: http://127.0.0.1:$DemoPort/dashboard"
