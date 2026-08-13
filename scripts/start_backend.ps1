# 后端守护脚本：uvicorn 意外退出（如 pyarrow 原生段错误 0xc0000005）后自动重启。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start_backend.ps1（保持窗口运行）
# 由 启动服务.bat / scripts\start_tunnel.ps1 间接调用；关闭本窗口即停止服务。
$ErrorActionPreference = "Continue"
Set-Location "H:\stock_review_crew"
$env:HTTP_PROXY = "http://127.0.0.1:7890"
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
# 2026-08-07??? shell/????? DEEPSEEK_* ?????config ? override=True
# ?? .env ?????????????????? Key ? load_dotenv ?????
Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:DEEPSEEK_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:DEEPSEEK_API_BASE -ErrorAction SilentlyContinue
Remove-Item Env:DEEPSEEK_MODEL -ErrorAction SilentlyContinue
$logDir = "H:\stock_review_crew\.tmp\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# ── Langfuse 启动检查（2026-08-06：每次启动打开监控）──
$envFile = "H:\stock_review_crew\.env"
$lfOk = $false
if (Test-Path $envFile) {
    $envText = Get-Content -LiteralPath $envFile -Raw
    $hasPub = $envText -match '(?m)^\s*LANGFUSE_PUBLIC_KEY\s*=\s*\S'
    $hasSec = $envText -match '(?m)^\s*LANGFUSE_SECRET_KEY\s*=\s*\S'
    $hasHost = $envText -match '(?m)^\s*LANGFUSE_(?:BASE_URL|HOST)\s*=\s*\S'
    $lfOk = $hasPub -and $hasSec -and $hasHost
}
if ($lfOk) {
    Write-Host "[Langfuse] 凭据齐全：Langfuse 监控将随服务启动自动打开（/api/health 可查看状态）"
} else {
    Write-Host "[Langfuse] 警告：.env 缺少 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL，" `
        "Langfuse 监控未打开（可在 .env 补齐后重启）" -ForegroundColor Yellow
}

# 启动前先清理 8501 旧实例，确保加载最新代码（PLAYBOOK：旧实例未重启 → 用户看到旧行为）
$existing = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "停止 8501 旧实例 PID $($existing.OwningProcess -join ',')..."
    Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}


# ?? Start-Process ????????2026-08-06???
# ???????/?????????? Path ? PATH ?????????
# Start-Process ???????????????????????:Path/PATH??
# ?? uvicorn ???????????? PATH???? $proc ??????
$pathVals = @()
foreach ($k in 'Path', 'PATH') {
    $item = Get-Item "Env:$k" -ErrorAction SilentlyContinue
    if ($item) { $pathVals += $item.Value }
}
$mergedPath = (($pathVals -join ';') -split ';' | Where-Object { $_ -ne '' } | Select-Object -Unique) -join ';'
Remove-Item Env:Path -ErrorAction SilentlyContinue
Remove-Item Env:PATH -ErrorAction SilentlyContinue
$env:PATH = $mergedPath

while ($true) {
    $proc = Start-Process "H:\stock_review_crew\.venv\Scripts\python.exe" `
        -ArgumentList "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8501" `
        -WorkingDirectory "H:\stock_review_crew" -WindowStyle Hidden `
        -RedirectStandardOutput "$logDir\uvicorn_watch.out.log" `
        -RedirectStandardError "$logDir\uvicorn_watch.err.log" -PassThru
    Write-Host ("[watchdog] uvicorn 已启动 PID {0} @ {1}" -f $proc.Id, (Get-Date -Format "HH:mm:ss"))
    if ($null -eq $proc) {
        Write-Host "[watchdog] Start-Process ?? uvicorn ???$Error[0]??5 ????..."
        Start-Sleep -Seconds 5
        continue
    }
    $proc.WaitForExit()
    $code = $proc.ExitCode
    Write-Host ("[watchdog] uvicorn 退出（code {0}）@ {1}，3 秒后自动重启..." -f $code, (Get-Date -Format "HH:mm:ss"))
    Start-Sleep -Seconds 3
}
