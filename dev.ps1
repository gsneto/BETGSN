# BETGSN — sobe backend (FastAPI) e frontend (Vite) em janelas separadas.
#
#   .\dev.ps1
#
# Frontend: http://localhost:5180
# Backend:  http://127.0.0.1:8787  (docs em /docs)
#
# A porta 5180 e dedicada ao BETGSN para nao conflitar com outros projetos
# que costumam usar a 5173. Para trocar: .\dev.ps1 -WebPort 5190

param(
    [int]$WebPort = 5180,
    [int]$ApiPort = 8787
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$web = Join-Path $root "web"

if (-not (Test-Path $python)) {
    Write-Host "ERRO: venv nao encontrado em .venv" -ForegroundColor Red
    Write-Host "Crie e instale as dependencias da API:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\pip install -r requirements-api.txt"
    exit 1
}

if (-not (Test-Path (Join-Path $web "node_modules"))) {
    Write-Host "node_modules ausente — rodando npm install em web\ ..." -ForegroundColor Yellow
    Push-Location $web
    npm install
    Pop-Location
}

Write-Host "Subindo BETGSN..." -ForegroundColor Cyan

Start-Process -FilePath $python `
    -ArgumentList "betgsn.py", "--api", "--port=$ApiPort" `
    -WorkingDirectory $root `
    -WindowStyle Minimized

Start-Sleep -Seconds 3

$env:BETGSN_WEB_PORT = "$WebPort"
Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/k", "npm run dev" `
    -WorkingDirectory $web

Start-Sleep -Seconds 5

Write-Host ""
Write-Host "BETGSN no ar:" -ForegroundColor Green
Write-Host "  Frontend  http://localhost:$WebPort"
Write-Host "  Backend   http://127.0.0.1:$ApiPort/docs"
Write-Host ""
Write-Host "Feche as janelas abertas para encerrar os servidores."
