# BETGSN - Para o terminal (backend + frontend + engine realtime).
#
#   .\stop.ps1
#
# Ordem: engine (via API, graceful) -> backend (porta 8787) ->
# frontend (porta 5180). Nao mata processos que nao sejam do BETGSN:
# so encerra quem ESTA OUVINDO nas portas do produto.

param(
    [int]$WebPort = 5180,
    [int]$ApiPort = 8787,
    #: So encerra quem o stop reconhece como BETGSN. Use -Force para
    #: derrubar qualquer processo que ocupe a porta (sem checar identidade).
    [switch]$Force
)

$ErrorActionPreference = "Continue"
$apiUrl = "http://127.0.0.1:$ApiPort"

# Identidade: o processo realmente e do BETGSN? Sem isso, o stop derrubaria
# um processo alheio que por acaso use a porta 5180/8787.
function Test-BetgsnProcess([int]$ProcessId) {
    if ($Force) { return $true }
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    } catch {
        return $false
    }
    $cmd = [string]$proc.CommandLine
    if ($proc.Name -match "python") { return $cmd -match "betgsn" }
    if ($proc.Name -match "node") { return ($cmd -match "vite") -or ($cmd -match "betgsn-web") }
    return $false
}

function Stop-BetgsnOnPort([int]$Port, [string]$Label) {
    $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "$Label`: ja estava fora" -ForegroundColor DarkGray
        return
    }
    foreach ($procId in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
        if (-not (Test-BetgsnProcess $procId)) {
            Write-Host "$Label (pid $procId): NAO e do BETGSN — preservado (use -Force para derrubar)" -ForegroundColor Yellow
            continue
        }
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "$Label (pid $procId): encerrado" -ForegroundColor Green
        } catch {
            Write-Host "$Label (pid $procId): nao foi possivel encerrar" -ForegroundColor Yellow
        }
    }
}

# 1. Engine realtime: shutdown graceful pela API (encerra a thread de captura)
try {
    $null = Invoke-RestMethod -Uri "$apiUrl/api/realtime/stop" -Method Post -TimeoutSec 10
    Write-Host "Realtime engine: parado (graceful)" -ForegroundColor Green
} catch {
    Write-Host "Realtime engine: nao respondeu (ja parado ou backend fora)" -ForegroundColor DarkGray
}

# 2. Backend: encerra SOMENTE processos do BETGSN ouvindo a porta da API
Stop-BetgsnOnPort -Port $ApiPort -Label "Backend"

# 3. Frontend: encerra SOMENTE processos do BETGSN ouvindo a porta do Vite
Stop-BetgsnOnPort -Port $WebPort -Label "Frontend"

Write-Host ""
Write-Host "BETGSN parado. Logs operacionais em output\logs\." -ForegroundColor Cyan
