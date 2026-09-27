@echo off
title LTX-2.5 Status pruefen
cd /d "%~dp0"
echo Frage Status/History fuer prompt_id d30bd251-fe42-49bc-982f-517d2ffa19e0 ab ...
echo.
curl -s http://127.0.0.1:8188/history/d30bd251-fe42-49bc-982f-517d2ffa19e0
echo.
echo.
echo ------------------------------------------------------------
echo Fertig. Bitte kompletten Text oben hier kopieren.
pause
