@echo off
title LTX-2.5 Testauftrag an ComfyUI senden
cd /d "%~dp0"
echo Sende Testauftrag (ltx25_prompt_payload.json) an ComfyUI auf 127.0.0.1:8188 ...
echo.
curl -X POST http://127.0.0.1:8188/prompt -H "Content-Type: application/json" --data-binary "@ltx25_prompt_payload.json"
echo.
echo.
echo Fertig. Falls oben ein "prompt_id" erschienen ist, wurde der Auftrag angenommen.
echo Falls ein Fehler ("error"/"node_errors") erschienen ist, bitte den Text hier kopieren.
pause
