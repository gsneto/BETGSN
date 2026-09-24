# BETGSN — captura diária de odds (script operacional).
#
# Wrapper FINO em torno do CLI existente (`betgsn.py --capture-odds`):
# nada de core, nada de chaves aqui (o CLI lê o .env). Idempotente por
# construção — o store grava com INSERT OR IGNORE na chave física
# (partida, mercado, resultado, casa, timestamp) e a captura deduplica
# o lote antes de gravar. Falha de um provider não interrompe os demais
# (o CLI registra sucesso/falha por provider e segue).
#
# Após a captura, roda o RELATÓRIO de CLV (`tools/clv_report.py`):
# leitura idempotente do store (sweep do lifecycle PENDING/NO_CLOSE/
# CLOSED/INVALID/MISMATCH) — é o passo que transforma capturas novas
# em fechamentos classificados. Rodar o script de novo não duplica
# nada (nada é gravado pelo relatório). Use -SkipClvReport para pular.
#
# CADÊNCIA IMPORTANTE para o CLV: o fechamento exige observação dentro
# da janela de 120 min do kickoff (CLOSING_WINDOW_MINUTES). Uma única
# captura de manhã deixa a maioria dos jogos fora da janela (NO_CLOSE
# por ausência, não por falha). Para acumular fechamentos reais,
# agende o script VÁRIAS vezes ao dia (ex.: horário em dias de jogo):
#
#   schtasks /Create /SC HOURLY /TN BETGSN-capture-odds ^
#     /TR "pwsh -NoProfile -File C:\...\BETGSN\scripts\capture_daily.ps1"
#
# Uso manual:
#   .\scripts\capture_daily.ps1
#   .\scripts\capture_daily.ps1 -Providers "The Odds API,ParlayAPI,OddsPapi"
#   .\scripts\capture_daily.ps1 -SkipClvReport
#
# Agendamento diário (Task Scheduler) — configurar EXPLICITAMENTE, este
# script NÃO cria tarefa sozinho:
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
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    # Pular o relatório de CLV pós-captura (default: rodar).
    [switch]$SkipClvReport
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
    $exitCode = $LASTEXITCODE

    # ---- sweep + relatório de CLV (leitura idempotente) ----
    # Transforma as observações recém-capturadas em fechamentos
    # classificados (PENDING -> CLOSED/NO_CLOSE conforme o observado).
    # NÃO altera o código de saída da captura: falha do relatório é
    # avisada, mas a captura em si é o que importa aqui.
    if (-not $SkipClvReport) {
        Write-Output ""
        Write-Output "=== CLV lifecycle sweep (pós-captura, leitura) ==="
        try {
            & $python "tools\clv_report.py" 2>&1 | Tee-Object -FilePath $log -Append
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "relatório de CLV terminou com código $LASTEXITCODE (captura não afetada)"
            }
        } catch {
            Write-Warning "falha ao rodar tools/clv_report.py: $_ (captura não afetada)"
        }
    }
    exit $exitCode
} finally {
    Pop-Location
}
