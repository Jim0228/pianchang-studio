$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw '未找到 .venv。请先执行：python -m venv .venv 并安装 backend\requirements.txt'
}

if (-not (Test-Path (Join-Path $root 'web\dist\index.html'))) {
    throw '前端尚未构建。请先执行：pnpm --dir web build'
}

Write-Host '片场 Studio 启动中：http://127.0.0.1:8000' -ForegroundColor Cyan
& $python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
