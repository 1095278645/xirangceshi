# 一键复位演示环境：清空并重灌演示数据（同时清掉 AI 指标），可选预热缓存。
# 用法（仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\demo_reset.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\demo_reset.ps1 -Warm
#
# 说明：seed_demo_data.py --force 会先删除 server/data/ai_shopkeeper.db（含 AI 指标表），
# 再重新生成演示数据 —— 因此不需要单独清理。
[CmdletBinding()]
param([switch]$Warm, [int]$Port = 8000)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root "server"

Write-Host "== 复位演示数据（删库重灌） ==" -ForegroundColor Cyan
Push-Location $Server
try {
    python ..\scripts\seed_demo_data.py --force
    if ($Warm) {
        Write-Host "`n== 预热 AI 缓存（需后端已启动） ==" -ForegroundColor Cyan
        python ..\scripts\prewarm_cache.py
    }
} finally { Pop-Location }

Write-Host "`n完成。提示：重启后端或等下次请求即可看到全新的演示数据。" -ForegroundColor Green
