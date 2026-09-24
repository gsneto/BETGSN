# BETGSN - Estado do sistema (o sistema esta vivo?).
#
#   .\status.ps1
#
# Mostra: backend, frontend, realtime engine (providers, ticks, ultimos
# eventos) — tudo lido da API real, nada inventado.

param(
    [int]$WebPort = 5180,
    [int]$ApiPort = 8787
)

$apiUrl = "http://127.0.0.1:$ApiPort"

function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

Write-Host "=== BETGSN STATUS ===" -ForegroundColor Cyan
Write-Host ""

$backendUp = Test-Port $ApiPort
$frontendUp = Test-Port $WebPort
Write-Host ("Backend  : " + $(if ($backendUp) { "NO AR ($apiUrl)" } else { "FORA" })) $(if ($backendUp) { -ForegroundColor Green } else { -ForegroundColor Red })
Write-Host ("Frontend : " + $(if ($frontendUp) { "NO AR (http://localhost:$WebPort)" } else { "FORA" })) $(if ($frontendUp) { -ForegroundColor Green } else { -ForegroundColor Red })

if (-not $backendUp) {
    Write-Host ""
    Write-Host "Backend fora: rode .\start.ps1" -ForegroundColor Yellow
    exit 0
}

Write-Host ""
try {
    $rt = Invoke-RestMethod -Uri "$apiUrl/api/realtime/status" -TimeoutSec 5
    if ($null -eq $rt.engine) {
        Write-Host "Realtime : inicializando (bootstrap em background)" -ForegroundColor Yellow
        if ($rt.boot.error) {
            Write-Host "  ERRO de bootstrap: $($rt.boot.error)" -ForegroundColor Red
        }
    } else {
        $e = $rt.engine
        $state = if ($e.running) { "RODANDO" } else { "PARADO" }
        $color = if ($e.running) { "Green" } else { "Red" }
        Write-Host "Realtime : $state" -ForegroundColor $color
        Write-Host "  sport keys   : $($e.sport_keys -join ', ')"
        Write-Host "  intervalo    : $($e.interval_seconds)s por tick"
        Write-Host "  eventos      : $($e.state.events) ($($e.state.events_matched) casados, $($e.state.events_unmatched) unmatched)"
        Write-Host "  linhas       : $($e.state.lines)"
        Write-Host "  sinais ativos: $($e.signals.active)"
        Write-Host "  problemas    : $($e.state.problems)"
        Write-Host "  ultima quote : $($e.last_quote_at)"
        Write-Host "  ultimo mov.  : $($e.last_movement_at)"
        Write-Host "  ultimo sinal : $($e.last_signal_at)"
        Write-Host "  heartbeat    : $($e.last_heartbeat)"
        if ($e.last_error) {
            Write-Host "  ultimo erro  : $($e.last_error.Substring(0, [Math]::Min(200, $e.last_error.Length)))" -ForegroundColor Yellow
        }
        Write-Host "  providers:"
        foreach ($prop in $e.providers.PSObject.Properties) {
            $p = $prop.Value
            $health = if ($p.last_error -and (-not $p.last_success_at -or $p.last_failure_at -gt $p.last_success_at)) { "COM ERRO" } else { "HEALTHY" }
            $hcolor = if ($health -eq "HEALTHY") { "Green" } else { "Red" }
            Write-Host ("    {0,-14} {1,-10} ticks={2} falhas={3} ultimo tick={4}" -f $p.provider, $health, $p.ticks, $p.failures, $p.last_tick_at) -ForegroundColor $hcolor
            if ($p.last_error) {
                Write-Host "      erro: $($p.last_error.Substring(0, [Math]::Min(160, $p.last_error.Length)))" -ForegroundColor DarkYellow
            }
        }
    }
} catch {
    Write-Host "Realtime : endpoint nao respondeu ($($_.Exception.Message))" -ForegroundColor Red
}

Write-Host ""
Write-Host "SSE stream: $apiUrl/api/realtime/stream (aba LIVE do frontend)" -ForegroundColor DarkCyan
Write-Host "Parar tudo: .\stop.ps1   |   Iniciar: .\start.ps1" -ForegroundColor DarkCyan
