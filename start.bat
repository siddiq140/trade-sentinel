@echo off
rem Start the trade-sentinel dashboard and scheduler: http://127.0.0.1:8247
cd /d "%~dp0"
if not exist trader-247\config.json python skills\trade-sentinel\engine.py init --preset nse
findstr /c:"\"series\"" trader-247\universe.json >nul 2>&1 || python skills\trade-sentinel\engine.py universe
python skills\trade-sentinel\server.py %*
