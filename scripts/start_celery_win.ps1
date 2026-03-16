# Windows 下启动 Celery worker（需先激活 .venv 并已启动 Redis）
# 在项目根目录执行: .\scripts\start_celery_win.ps1
# 或先 cd 到项目根目录再: & "$PSScriptRoot\start_celery_win.ps1"

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $ProjectRoot "app"))) {
    $ProjectRoot = Get-Location
}
Set-Location $ProjectRoot

# Windows 不支持 fork，必须使用 --pool=solo（单进程）或 --pool=threads
celery -A app.core.celery_app:celery_app worker -l INFO -Q compliance_scan --pool=solo
