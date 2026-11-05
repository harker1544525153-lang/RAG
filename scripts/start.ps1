# ===== RAG 智能文档问答系统 · 一键启动（Windows PowerShell）=====
# 流程：选择解释器 → 检查 .env → 检查前端产物 → 端口预检（自动结束上次残留的本项目后端）→ 启动
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# HuggingFace 镜像（仅在 .env 未配置时兜底，首次下载模型时更稳）
if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = 'https://hf-mirror.com' }

Write-Host '===== RAG 智能文档问答系统 =====' -ForegroundColor Cyan

# 1. 选择 Python：优先项目内 venv（依赖最完整），否则用 PATH 里的 python
$py = Join-Path $root 'backend\venv\Scripts\python.exe'
if (Test-Path $py) {
    Write-Host '[1/4] 解释器：backend\venv' -ForegroundColor Gray
} else {
    $py = 'python'
    Write-Host '[1/4] 未找到 backend\venv，使用 PATH 中的 python' -ForegroundColor Yellow
}

# 2. 检查 .env
$envFile = Join-Path $root 'backend\.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root 'backend\.env.example') $envFile
    Write-Host '[2/4] 已生成 backend\.env，请填写 DASHCOPE_API_KEY' -ForegroundColor Yellow
} else {
    Write-Host '[2/4] 配置文件 backend\.env 已存在' -ForegroundColor Gray
}

# 3. 检查前端产物，缺失则构建
$dist = Join-Path $root 'frontend\dist\index.html'
if (-not (Test-Path $dist)) {
    Write-Host '[3/4] 未找到 frontend\dist，开始构建...' -ForegroundColor Yellow
    Push-Location (Join-Path $root 'frontend')
    try {
        if (-not (Test-Path 'node_modules')) { & npm install --no-audit --no-fund }
        & npm run build
    } finally { Pop-Location }
} else {
    Write-Host '[3/4] 前端产物已就绪（frontend\dist）' -ForegroundColor Gray
}

# 4. 端口预检：端口取自 backend\.env 的 PORT（缺省 8080）；被占用时，确认是本项目后端则结束，避免 WinError 10048
$port = 8080
$envFile = Join-Path $root 'backend\.env'
if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}
$conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $procId = ($conn | Select-Object -First 1).OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId"
    $cmd = $proc.CommandLine
    if ($cmd -and $cmd -match 'app\.main') {
        Write-Host "[4/4] 发现上一次的后端进程（PID=$procId），正在结束..." -ForegroundColor Yellow
        Stop-Process -Id $procId -Force
        Start-Sleep -Seconds 2
    } else {
        Write-Host "[4/4] 端口 $port 被 PID=$procId（$($proc.Name)）占用，且不是本项目的后端。" -ForegroundColor Red
        Write-Host '      请先关闭该程序，或修改 backend\.env 的 PORT 和 frontend/vite.config.js 的代理端口。' -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[4/4] 端口 $port 可用" -ForegroundColor Gray
}

Write-Host ''
Write-Host "用户端: http://localhost:$port/" -ForegroundColor Green
Write-Host "管理端: http://localhost:$port/admin/" -ForegroundColor Green
Write-Host '按 Ctrl+C 停止服务' -ForegroundColor Gray
Write-Host ''

Push-Location (Join-Path $root 'backend')
try { & $py -m app.main } finally { Pop-Location }
