@echo off
SET VENV_NAME=.venv

cd D:\work\my-ai-coder

:: 1. 检查虚拟环境目录是否存在
if not exist %VENV_NAME% (
    echo [INFO] 正在创建虚拟环境...
    python -m venv %VENV_NAME%
    echo [SUCCESS] 虚拟环境已创建。
) else (
    echo [INFO] 虚拟环境已存在，准备激活...
)

:: 2. 激活虚拟环境并保持窗口开启
:: 使用 /k 参数让 cmd 在执行完激活命令后不关闭
echo [INFO] 正在进入虚拟环境...
cmd /k ".\%VENV_NAME%\Scripts\activate"