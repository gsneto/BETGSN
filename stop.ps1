# BETGSN - Para o terminal (backend + frontend + engine realtime).
#
#   .\stop.ps1
#
# Ordem: engine (via API, graceful) -> backend (porta 8787) ->
# frontend (porta 5180). Nao mata processos que nao sejam do BETGSN:
# so encerra quem ESTA OUVINDO nas portas do produto.

param(
    [int]$WebPort = 5180,
    [int]$ApiPort = 8787
)

$ErrorActionPreference = "Continue"
$apiUrl = "http://127.0.0.1:$ApiPort"

# 1. Engine realtime: shutdown graceful pela API (encerra a thread de captura)
try {
    $null = Invoke-RestMethod -Uri "$apiUrl/api/realtime/stop" -Method Post -TimeoutSec 10
    Write-Host "Realtime engine: parado (graceful)" -ForegroundColor Green
} catch {
    Write-Host "Realtime engine: nao respondeu (ja parado ou backend fora)" -ForegroundColor DarkGray
}

# 2. Backend: encerra os processos que ouvem a porta da API
$apiConns = Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue
if ($apiConns) {
    $apiConns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        try {
            Stop-Process -Id $_ -Force -ErrorAction Stop
            Write-Host "Backend (pid $_): encerrado" -ForegroundColor Green
        } catch {
            Write-Host "Backend (pid $_): nao foi possivel encerrar" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "Backend: ja estava fora" -ForegroundColor DarkGray
}

# 3. Frontend: encerra quem ouve a porta do Vite (node)
$webConns = Get-NetTCPConnection -State Listen -LocalPort $WebPort -ErrorAction SilentlyContinue
if ($webConns) {
    $webConns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        try {
            Stop-Process -Id $_ -Force -ErrorAction Stop
            Write-Host "Frontend (pid $_): encerrado" -ForegroundColor Green
        } catch {
            Write-Host "Frontend (pid $_): nao foi possivel encerrar" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "Frontend: ja estava fora" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "BETGSN parado. Logs operacionais em output\logs\." -ForegroundColor Cyan
