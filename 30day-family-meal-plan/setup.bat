@echo off
chcp 65001 > nul

echo ======================================
echo 🍳 30天低碳水家庭菜谱计划
echo ======================================
echo.

REM 检查Python
python --version > nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到Python，请先安装Python 3.8+
    echo 访问 https://www.python.org 下载安装
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo ✅ Python版本: %PYTHON_VERSION%
echo.

REM 检查虚拟环境
if not exist "venv\" (
    echo 📦 创建虚拟环境...
    python -m venv venv
)

echo ✅ 虚拟环境已准备
echo.

REM 激活虚拟环境
call venv\Scripts\activate.bat

REM 安装依赖
echo 📥 安装依赖...
pip install -r requirements.txt -q
echo ✅ 依赖安装完成
echo.

echo ======================================
echo 🚀 启动应用
echo ======================================
echo.
echo 打开浏览器访问: http://localhost:8501
echo.
echo 按 Ctrl+C 停止应用
echo.

streamlit run app.py

pause
