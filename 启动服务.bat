@echo off

chcp 65001 >nul

cd /d "%~dp0"

echo ============================================

echo    A股多智能体复盘系统 - 本地服务启动

echo ============================================

netstat -ano | findstr /C:":8501" | findstr LISTENING >nul

if not errorlevel 1 (

    echo [已运行] 后端服务已在 8501 端口运行

) else (

    echo [启动] 正在启动后端服务（.venv / uvicorn / 8501）...

    set "HTTP_PROXY=http://127.0.0.1:7890"

    set "HTTPS_PROXY=http://127.0.0.1:7890"

    start "StockReviewBackend" /min cmd /c "cd /d %~dp0 && powershell -ExecutionPolicy Bypass -File scripts\start_backend.ps1"

    timeout /t 6 /nobreak >nul

)

echo [完成] 正在打开页面：http://127.0.0.1:8501

start http://127.0.0.1:8501

echo.

echo 提示：后端日志在最小化窗口里，关闭该窗口即停止服务。

echo 需要公网访问时，双击「启动隧道.bat」。

pause

