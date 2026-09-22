# One-click demo launch for Windows PowerShell.
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\demo_up.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\demo_up.ps1 -ForceSeed -SkipWarm
#
# Steps: check python -> seed demo data (if needed) -> start backend ->
#        print LAN IP -> (best effort) firewall rule -> prewarm AI cache ->
#        run pre-demo self-check.
[CmdletBinding()]
param(
    [switch]$ForceSeed,
    [switch]$SkipWarm,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root "server"
$Db = Join-Path $Server "data\ai_shopkeeper.db"
$Log = Join-Path $Server "data\uvicorn.out.log"
$Err = Join-Path $Server "data\uvicorn.err.log"

function Write-Step($msg) { Write-Host "`n== $msg ==" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Ok($msg) { Write-Host "  + $msg" -ForegroundColor Green }

Write-Step "1/6 Check Python"
try { $pyv = & python --version 2>&1; Ok $pyv } catch { throw "python not found in PATH" }

Write-Step "2/6 Demo data"
if ($ForceSeed -or -not (Test-Path $Db)) {
    Push-Location $Server
    try {
        if ($ForceSeed) { python ..\scripts\seed_demo_data.py --force }
        else { python ..\scripts\seed_demo_data.py }
    } finally { Pop-Location }
} else {
    Ok "demo db already exists (use -ForceSeed to reseed): $Db"
}

Write-Step "3/6 Start backend on 0.0.0.0:$Port"
$already = $false
try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 | Out-Null; $already = $true } catch { }
if ($already) {
    Ok "backend already running on port $Port (reusing)"
} else {
    $p = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "$Port") `
        -WorkingDirectory $Server -WindowStyle Hidden `
        -RedirectStandardOutput $Log -RedirectStandardError $Err -PassThru
    Set-Content -Path (Join-Path $Server "data\uvicorn.pid") -Value $p.Id
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try { if ((Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/health" -TimeoutSec 2).StatusCode -eq 200) { $ok = $true; break } } catch { }
    }
    if ($ok) { Ok "backend started (PID $($p.Id)); stop with: Stop-Process -Id $($p.Id)" }
    else { Warn "backend did not become healthy; see $Err"; Get-Content $Err -ErrorAction SilentlyContinue | Select-Object -Last 10 }
}

Write-Step "4/6 LAN address for the phone"
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress
if ($ips) { $ips | ForEach-Object { Ok "http://${_}:$Port" } } else { Warn "no LAN IPv4 found" }
Write-Host "  On the phone: set this URL in the app's settings page (More -> backend address)."

Write-Step "5/6 Firewall (best effort, needs admin)"
try {
    if (-not (Get-NetFirewallRule -DisplayName "AI Shopkeeper Demo" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "AI Shopkeeper Demo" -Direction Inbound `
            -LocalPort $Port -Protocol TCP -Action Allow -Profile Private | Out-Null
        Ok "firewall rule added for port $Port (Private profile)"
    } else { Ok "firewall rule already exists" }
} catch { Warn "could not add firewall rule (run PowerShell as admin if the phone cannot connect)" }

if (-not $SkipWarm) {
    Write-Step "6/6 Prewarm AI cache"
    Push-Location $Server
    try { python ..\scripts\prewarm_cache.py } finally { Pop-Location }
} else {
    Write-Step "6/6 Prewarm skipped (-SkipWarm)"
}

Write-Step "Pre-demo self-check"
Push-Location $Server
try { python ..\scripts\mp_demo_check.py; $code = $LASTEXITCODE } finally { Pop-Location }
if ($code -eq 0) { Ok "self-check passed: you are good to demo" } else { Warn "self-check reported problems (see above)" }

Write-Host "`nDone. Keep this backend running during the demo." -ForegroundColor Cyan
