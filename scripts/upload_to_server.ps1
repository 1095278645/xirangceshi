<#
.SYNOPSIS
    把当前工作树打包上传到 Linux 服务器（腾讯云轻量等），供 deploy/up.sh 一键部署。

.DESCRIPTION
    ⚠️ 传的是**当前工作树**（含未提交改动），不是 GitHub 上的提交 —— 本机 25 个改动不会自己跑上去。
    默认排除 `server/data`（账本）与 `server/config.local.json`（AI Key）以及 `.env`（访问令牌）；
    只有显式加 -WithData 才把账本与 Key 一起传上去（做演示库迁移时用）。
    打包后会自检压缩包里没有密钥/账本，发现即中止并删除压缩包。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\upload_to_server.ps1 -Server 1.2.3.4 -User ubuntu

.EXAMPLE
    # 连演示账本一起迁过去（含 server/config.local.json 里的 AI Key）
    powershell -ExecutionPolicy Bypass -File scripts\upload_to_server.ps1 -Server 1.2.3.4 -User ubuntu -WithData
#>
# 编码约定：本文件必须保存为 **UTF-8 with BOM**。
# Windows PowerShell 5.1 会把「无 BOM 的 UTF-8」当 ANSI 读，中文注释会让脚本解析中途失败（实测踩过）。
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$User = "ubuntu",
    [int]$Port = 22,
    [string]$RemoteDir = "xirang",
    [string]$KeyPath = "",
    [switch]$WithData,
    [switch]$KeepArchive
)

$ErrorActionPreference = "Stop"

function Say([string]$m)  { Write-Host "==> $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Die([string]$m)  { Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $repo "Dockerfile"))) { Die "找不到仓库根目录（$repo 下没有 Dockerfile）" }
if (-not (Test-Path (Join-Path $repo "deploy\up.sh"))) { Die "仓库里缺少 deploy\up.sh" }

foreach ($exe in @("tar", "scp", "ssh")) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
        Die "缺少 $exe（Windows 10 1809+ 自带 OpenSSH 客户端与 tar，请在「设置 → 应用 → 可选功能」里安装）"
    }
}

$stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$archive = Join-Path $env:TEMP "xirang-$stamp.tar.gz"

# PowerShell 里给原生程序传参要显式数组，避免引号被吃掉
$sshOpts = @("-p", "$Port", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15")
$scpOpts = @("-P", "$Port", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15")
if ($KeyPath) {
    if (-not (Test-Path $KeyPath)) { Die "找不到私钥文件：$KeyPath" }
    $sshOpts += @("-i", $KeyPath)
    $scpOpts += @("-i", $KeyPath)
}
$target = "$User@$Server"

# ---------- 1. 打包当前工作树 ----------
Say "打包当前工作树（排除 .git / 账本 / 密钥 / 演示材料）"
$tarArgs = @(
    "-czf", $archive,
    "-C", $repo,
    "--exclude=./.git",
    "--exclude=./server/data",
    "--exclude=./server/config.local.json",
    "--exclude=./server/models",
    "--exclude=./server/.venv",
    "--exclude=./server/.pytest_cache",
    "--exclude=./.env",
    "--exclude=./deliverables",
    "--exclude=./.playwright-mcp",
    "--exclude=./node_modules",
    "--exclude=*.pyc",
    "--exclude=__pycache__",
    "."
)
& tar @tarArgs
if ($LASTEXITCODE -ne 0) { Die "打包失败（tar 退出码 $LASTEXITCODE）" }

# ---------- 2. 防泄漏自检：压缩包里绝不能有账本/密钥 ----------
$leaked = @(& tar -tzf $archive | Where-Object {
    $_ -match '^\./server/data/' -or $_ -match 'config\.local\.json$' -or $_ -match '^\./\.env$' -or $_ -match '\.db$'
})
if ($leaked.Count -gt 0) {
    Remove-Item $archive -Force
    Die "压缩包里出现了账本或密钥（$($leaked[0]) …），已删除压缩包并中止。请检查 tar 排除规则。"
}
$sizeMB = [math]::Round((Get-Item $archive).Length / 1MB, 2)
Say "打包完成：$archive（$sizeMB MB，已确认不含账本与密钥）"

# ---------- 3. 上传并解包 ----------
Say "在服务器上准备目录 ~/$RemoteDir"
& ssh @sshOpts $target "mkdir -p '$RemoteDir'"
if ($LASTEXITCODE -ne 0) { Die "SSH 连接失败：请确认 IP、用户名、端口、密钥（云服务器安全组需放通 22）" }

Say "上传代码包（$sizeMB MB，国内服务器约 1~3 分钟）"
& scp @scpOpts $archive "${target}:$RemoteDir/"
if ($LASTEXITCODE -ne 0) { Die "上传失败（scp 退出码 $LASTEXITCODE）" }

Say "在服务器上解包"
& ssh @sshOpts $target "tar -xzf '$RemoteDir/$(Split-Path $archive -Leaf)' -C '$RemoteDir' && rm -f '$RemoteDir/$(Split-Path $archive -Leaf)' && echo unpacked"
if ($LASTEXITCODE -ne 0) { Die "解包失败" }

# ---------- 4. 可选：账本与 AI Key ----------
if ($WithData) {
    $dbFile = Join-Path $repo "server\data\ai_shopkeeper.db"
    if (Test-Path $dbFile) {
        Warn "按 -WithData 上传账本（server/data，含 SQLite 库与备份快照）与 server/config.local.json"
        & ssh @sshOpts $target "mkdir -p '$RemoteDir/server/data'"
        & scp @scpOpts -r (Join-Path $repo "server\data\.") "${target}:$RemoteDir/server/data/"
        if ($LASTEXITCODE -ne 0) { Die "账本上传失败（服务器上已有数据时请先在服务器备份再重试）" }
    } else {
        Warn "本机没有 server\data\ai_shopkeeper.db，跳过账本上传"
    }
    $cfg = Join-Path $repo "server\config.local.json"
    if (Test-Path $cfg) {
        & scp @scpOpts $cfg "${target}:$RemoteDir/server/"
        Warn "AI Key（config.local.json）已上传：请确认该服务器只有你能登录"
    }
} else {
    Say "未传账本与 AI Key（默认）：服务器上会是空库，AI 走规则降级，可在网页端「设置」页再填 Key"
}

if (-not $KeepArchive) { Remove-Item $archive -Force -ErrorAction SilentlyContinue }

# ---------- 5. 下一步 ----------
Write-Host ""
Write-Host "======== 本机侧完成 ========" -ForegroundColor Cyan
Write-Host "下一步（在服务器上执行）："
Write-Host "  ssh -p $Port $target"
Write-Host "  cd ~/$RemoteDir && bash deploy/up.sh"
Write-Host ""
Write-Host "有已备案域名时改成："
Write-Host "  cd ~/$RemoteDir && SITE_ADDRESS=shop.example.com bash deploy/up.sh"
