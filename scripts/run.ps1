# 启动控制台（PowerShell）
#   .\scripts\run.ps1            → http://127.0.0.1:8000
#   .\scripts\run.ps1 -Port 8080 -Reload

param([int]$Port = 8000, [switch]$Reload)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONIOENCODING = "utf-8"

$args = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port")
if ($Reload) { $args += "--reload" }

Write-Host "VoiceDub Studio → http://127.0.0.1:$Port" -ForegroundColor Cyan
& python @args
