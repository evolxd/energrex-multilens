#!/bin/bash

# 30天家庭菜谱项目 - 快速启动脚本

echo "======================================"
echo "🍳 30天低碳水家庭菜谱计划"
echo "======================================"
echo ""

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到Python3，请先安装Python 3.8+"
    exit 1
fi

echo "✅ Python版本: $(python3 --version)"
echo ""

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv venv
fi

echo "✅ 虚拟环境已准备"
echo ""

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "📥 安装依赖..."
pip install -r requirements.txt -q
echo "✅ 依赖安装完成"
echo ""

echo "======================================"
echo "🚀 启动应用"
echo "======================================"
echo ""
echo "打开浏览器访问: http://localhost:8501"
echo ""
echo "按 Ctrl+C 停止应用"
echo ""

streamlit run app.py
