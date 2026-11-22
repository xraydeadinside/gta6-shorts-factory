@echo off
chcp 65001 >nul
title Скачать с YouTube — помощник фабрики шортсов
cd /d "%~dp0"
node helper\youtube-helper.mjs
echo.
pause
