# 独立平台的 Windows 启动入口；只定位脚本自身所在目录，不依赖旧项目路径。
# 在当前终端前台运行，Ctrl+C 可触发 app.py 的正常退出与任务执行器关闭。
param(
    # 可用本机端口；与旧平台分开，默认 8770。
    [ValidateRange(1, 65535)]
    [int]$Port = 8770,
    # 使用项目内相对数据目录，真正的路径边界由后端再次检查。
    [string]$DataDirectory = 'data'
)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    # UTF-8 保证中文运行提示和异常信息在终端可读；不加载任何业务凭据。
    $env:PYTHONIOENCODING = 'utf-8'
    $platformPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $platformPython)) { throw '请先创建 .venv 并安装 requirements.lock.txt。' }
    & $platformPython app.py --port $Port --data $DataDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "平台退出，状态码：$LASTEXITCODE。请检查 Python 版本、端口和数据路径。"
    }
}
finally {
    # 无论服务成功退出或失败，都恢复调用者所在目录。
    Pop-Location
}
