param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& '.venv/Scripts/python.exe' -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $Port
