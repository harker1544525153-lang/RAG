@echo off
chcp 65001 >nul
REM ===== RAG 智能文档问答系统 · 一键启动（Windows）=====
REM 具体逻辑在 scripts\start.ps1：选择解释器、检查配置、构建前端、端口预检、启动后端
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
if errorlevel 1 pause
