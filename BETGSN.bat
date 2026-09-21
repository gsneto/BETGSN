@echo off
rem BETGSN - atalho de um clique. Abre o launcher que sobe backend+frontend e o navegador.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
