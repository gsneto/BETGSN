# BETGSN — captura diária de odds (script operacional).
#
# Wrapper FINO em torno do CLI existente (`betgsn.py --capture-odds`):
# nada de core, nada de chaves aqui (o CLI lê o .env). Idempotente por
# construção — o store grava com INSERT OR IGNORE na chave física
# (partida, mercado, resultado, casa, timestamp) e a captura deduplica
# o lote antes de gravar. Falha de um provider não interrompe os demais
# (o CLI registra sucesso/falha por provider e segue).
#
# Uso manual:
#   .\scripts\capture_daily.ps1
#   .\scripts\capture_daily.ps1 -Providers "The Odds API,ParlayAPI,OddsPapi"
#
# Agendamento (Task Scheduler) — configurar EXPLICITAMENTE, este script
# NÃO cria tarefa sozinho:
#   schtasks /Create /SC DAILY /ST 09:00 /TN BETGSN-capture-odds ^
#     /TR "pwsh -NoProfile -File C:\...\BETGSN\scripts\capture_daily.ps1"
#
# Saída: log em output\logs\capture_YYYYMMDD_HHMMSS.log + código de
# exit do CLI (0 ok / 1 erro de configuração / 2 captura com erros).

param(
    # Providers capturados (default: todos os configurados no .env).
    # Um provider que falha é registrado e os demais seguem.
    [string]$Providers = "",
    # Mercados pedidos aos providers (default do CLI: h2h,totals,btts).
    [string]$Markets = "",
    # Diretório do projeto (default: pai deste script).
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "venv não encontrado em $python (rodar: python -m venv .venv)"
    exit 1
}

$logDir = Join-Path $ProjectRoot "output\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logDir "capture_$stamp.log"

$cliArgs = @("betgsn.py", "--capture-odds")
if ($Providers) { $cliArgs += "--providers=$Providers" }
if ($Markets)   { $cliArgs += "--markets=$Markets" }

Write-Output "BETGSN captura de odds — $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output "log: $log"

Push-Location $ProjectRoot
try {
    & $python @cliArgs 2>&1 | Tee-Object -FilePath $log
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
