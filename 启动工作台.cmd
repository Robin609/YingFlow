@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo 请先按 README.md 安装本项目的 Python 环境。
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m video_app %*
