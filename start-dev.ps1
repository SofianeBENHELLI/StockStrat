<#
    start-dev.ps1 - launch the strategy-tournament dev stack.

    Opens the backend (FastAPI/uvicorn, port 8001) and frontend (Next.js, port
    3001) each in their OWN persistent PowerShell window, so they keep running
    independently of whatever shell launched them. Re-running is safe: any server
    already listening on its port is left alone.

    Usage:   right-click -> "Run with PowerShell", or:  ./start-dev.ps1
    Stop a server by closing its window (or Ctrl+C inside it).
#>

$ErrorActionPreference = 'Stop'

$Root        = $PSScriptRoot
$BackendDir  = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'

function Resolve-Tool {
    param([string]$Name, [string[]]$Fallbacks)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in $Fallbacks) { if (Test-Path $p) { return $p } }
    throw "Could not find '$Name'. Add it to PATH or edit start-dev.ps1."
}

function Test-Port {
    param([int]$Port)
    [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Start-Server {
    param([string]$Title, [int]$Port, [string]$WorkDir, [string]$Command)
    if (Test-Port -Port $Port) {
        Write-Host "[$Title] already running on port $Port - leaving it alone." -ForegroundColor Yellow
        return
    }
    Write-Host "[$Title] starting on port $Port ..." -ForegroundColor Cyan
    $inner = "Set-Location -LiteralPath '$WorkDir'; " + $Command
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList '-NoExit', '-NoProfile', '-Command', $inner `
        -WorkingDirectory $WorkDir | Out-Null
}

$uvFallback  = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\Scripts\uv.exe'
$uv  = Resolve-Tool -Name 'uv'  -Fallbacks @($uvFallback)
$npm = Resolve-Tool -Name 'npm' -Fallbacks @('C:\Program Files\nodejs\npm.cmd')

$backendCmd  = "& '$uv' run uvicorn app.main:app --host 0.0.0.0 --port 8001"
$frontendCmd = "& '$npm' run dev"

Start-Server -Title 'backend'  -Port 8001 -WorkDir $BackendDir  -Command $backendCmd
Start-Server -Title 'frontend' -Port 3001 -WorkDir $FrontendDir -Command $frontendCmd

Write-Host ""
Write-Host "Frontend:  http://localhost:3001" -ForegroundColor Green
Write-Host "Backend:   http://localhost:8001  (health: /api/health)" -ForegroundColor Green
Write-Host "Each server runs in its own window - close the window to stop it." -ForegroundColor DarkGray
