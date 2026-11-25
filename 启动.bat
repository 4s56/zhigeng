@echo off
chcp 65001 >nul
title 知更 · 个人智能体
cd /d "%~dp0"
echo.
echo   正在启动「知更」...
echo.
py server.py
echo.
echo   已停止。按任意键关闭窗口。
pause >nul
