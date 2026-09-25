# BETGSN - Launcher de um clique.
#
#   .\start.ps1            (ou o atalho "BETGSN" na area de trabalho)
#
# O que ele faz:
#   1. sobe o backend (FastAPI) se ainda nao estiver rodando;
#   2. sobe o frontend (Vite) se ainda nao estiver rodando;
#   3. espera os dois responderem;
#   4. abre o navegador em http://localhost:5180.
#
# Reabrir nao duplica processos: se ja estiver no ar, apenas abre o navegador.

param(
    [int]$WebPort = 5180,
    [int]$ApiPort = 8787
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$web = Join-Path $root "web"
$url = "http://localhost:$WebPort"
$apiUrl = "http://127.0.0.1:$ApiPort"

function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

if (-not (Test-Path $python)) {
    Write-Host "ERRO: venv nao encontrado em .venv" -ForegroundColor Red
    Write-Host "  python -m venv .venv" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\pip install -r requirements-api.txt" -ForegroundColor Yellow
    Read-Host "Pressione Enter para sair"
    exit 1
}

if (-not (Test-Path (Join-Path $web "node_modules"))) {
    Write-Host "node_modules ausente - rodando npm install em web\ ..." -ForegroundColor Yellow
    Push-Location $web
    npm install
    Pop-Location
}

# 1. Backend
if (Test-Port $ApiPort) {
    Write-Host "Backend ja no ar em $apiUrl" -ForegroundColor Cyan
} else {
    Write-Host "Subindo backend..." -ForegroundColor Cyan
    Start-Process -FilePath $python `
        -ArgumentList "betgsn.py", "--api", "--port=$ApiPort" `
        -WorkingDirectory $root -WindowStyle Minimized
}

# 2. Frontend
if (Test-Port $WebPort) {
    Write-Host "Frontend ja no ar em $url" -ForegroundColor Cyan
} else {
    Write-Host "Subindo frontend..." -ForegroundColor Cyan
    $env:BETGSN_WEB_PORT = "$WebPort"
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/k", "npm run dev" `
        -WorkingDirectory $web -WindowStyle Minimized
}

# 3. Espera os dois responderem
Write-Host "Aguardando os servidores responderem..." -ForegroundColor Cyan
$backendUp = $false
$frontendUp = $false
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    $backendUp = $false
    $frontendUp = $false
    try {
        $null = Invoke-RestMethod -Uri "$apiUrl/api/health" -TimeoutSec 2
        $backendUp = $true
    } catch {}
    try {
        $null = Invoke-WebRequest -Uri $url -TimeoutSec 2 -UseBasicParsing
        $frontendUp = $true
    } catch {}
    if ($backendUp -and $frontendUp) { break }
    Start-Sleep -Seconds 2
}

# 4. Estado do REALTIME ENGINE (sobe junto com o backend, a menos que
#    BETGSN_REALTIME=0). O bootstrap e em background: pode levar alguns
#    segundos carregando fixtures.
$realtime = "inicializando..."
try {
    $rt = Invoke-RestMethod -Uri "$apiUrl/api/realtime/status" -TimeoutSec 3
    if ($null -eq $rt.engine) {
        $realtime = "aguardando bootstrap"
    } elseif ($rt.engine.running) {
        $providers = ($rt.engine.providers.PSObject.Properties.Name) -join ", "
        $realtime = "RODANDO (providers: $providers)"
    } else {
        $realtime = "PARADO (POST $apiUrl/api/realtime/start)"
    }
} catch {
    $realtime = "indisponivel (backend antigo?)"
}

# 5. Veredito HONESTO: nao anunciar "no ar" se algum servico nao subiu.
if (-not ($backendUp -and $frontendUp)) {
    Write-Host ""
    Write-Host "BETGSN NAO SUBIU COMPLETO." -ForegroundColor Red
    if (-not $backendUp) {
        Write-Host "  Backend   NAO respondeu em $apiUrl/api/health" -ForegroundColor Red
        Write-Host "            veja a janela minimizada do python (erro de porta/dependencia)." -ForegroundColor Yellow
    } else {
        Write-Host "  Backend   OK  $apiUrl/docs" -ForegroundColor Green
    }
    if (-not $frontendUp) {
        Write-Host "  Frontend  NAO respondeu em $url" -ForegroundColor Red
        Write-Host "            veja a janela do npm (porta ocupada / erro de build)." -ForegroundColor Yellow
    } else {
        Write-Host "  Frontend  OK  $url" -ForegroundColor Green
    }
    Write-Host ""
    Write-Host "Navegador NAO foi aberto. Corrija o que faltou e rode .\start.ps1 de novo." -ForegroundColor Yellow
    Write-Host "Para PARAR TUDO: .\stop.ps1   |   Para ver estado: .\status.ps1"
    exit 1
}

Write-Host ""
Write-Host "BETGSN no ar:" -ForegroundColor Green
Write-Host "  Frontend  $url  (aba LIVE = terminal em tempo real)" -ForegroundColor Green
Write-Host "  Backend   $apiUrl/docs" -ForegroundColor Green
Write-Host "  Realtime  $realtime" -ForegroundColor Green
Write-Host "  Health    $apiUrl/api/realtime/status" -ForegroundColor Green
Write-Host ""
Write-Host "Para PARAR TUDO: .\stop.ps1   |   Para ver estado: .\status.ps1"
Write-Host "Navegador aberto. Feche as janelas minimizadas para encerrar os servidores."

Start-Process $url
