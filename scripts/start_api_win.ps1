# Windows 下启动网关 API（需先激活 .venv）
# 在项目根目录执行: .\scripts\start_api_win.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $ProjectRoot "app"))) {
    $ProjectRoot = Get-Location
}
Set-Location $ProjectRoot

uvicorn app.main:app --host 0.0.0.0 --port 8000
