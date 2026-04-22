@echo off
set CONFIG_PATH=%AICF_PROVIDER_CONFIG%
if "%CONFIG_PATH%"=="" set CONFIG_PATH=provider.config.json
python worker.py benchmark --config %CONFIG_PATH%
