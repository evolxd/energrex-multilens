@echo off
REM 2026-09-21：这里原来写死 `cd /d C:\Users\evolx\ai_valuation`。
REM 那条路径指向的是旧克隆——机器上同时存在两份这个项目的拷贝，用户在一份
REM 里 git pull、双击这个脚本启动的却是另一份，于是"拉了代码但页面没变"反复
REM 发生过好几轮。改成 %~dp0（脚本自己所在的目录），脚本跟代码就永远在一起，
REM 放到哪个克隆里就启动哪个克隆，不会再指错。start_chrome.bat 一直是这么写的。
cd /d "%~dp0"
echo 启动目录: %CD%
python -X utf8 -m streamlit run home.py --server.port 8501
