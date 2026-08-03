@echo off
REM 用 Inno Setup 编译器把 dist\KaomojiAssistant 封装为安装程序。
REM 前置：1) 已安装 Inno Setup；2) 已运行 build.py 生成 dist\KaomojiAssistant。
REM 若 iscc 不在 PATH 中，请改为 Inno Setup 安装目录下的完整路径，例如：
REM "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\KaomojiAssistant.iss

iscc installer\KaomojiAssistant.iss
