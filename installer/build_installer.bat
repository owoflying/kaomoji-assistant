@echo off
REM 用 Inno Setup 把 dist\KaomojiAssistant 封装为单文件安装程序。
REM
REM 前置：
REM   1) 已运行 python build.py 生成 dist\KaomojiAssistant\（会同时生成 app.ico）
REM   2) 已安装 Inno Setup（https://jrsoftware.org/isdl.php）
REM
REM 三种运行方式任选其一：
REM   a) 直接双击本文件（要求 iscc 在 PATH 中）
REM   b) 开始菜单打开「Inno Setup Compiler」-> File -> Open 打开本 .iss -> 点 Build
REM   c) 命令行手动指定 ISCC 完整路径：
REM      "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\KaomojiAssistant.iss

setlocal
set "ISS=installer\KaomojiAssistant.iss"

REM 1) 先试 PATH 中的 iscc
where iscc >nul 2>nul
if %errorlevel%==0 (
    iscc "%ISS%"
    goto :done
)

REM 2) 再试常见安装路径
set "ISCC_PATH="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe"      set "ISCC_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe" set "ISCC_PATH=%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"

if not "%ISCC_PATH%"=="" (
    "%ISCC_PATH%" "%ISS%"
    goto :done
)

REM 3) 都没找到，给出可读提示
echo.
echo [错误] 未检测到 Inno Setup（iscc 不在 PATH，也未在常见路径找到）。
echo 请先安装 Inno Setup： https://jrsoftware.org/isdl.php
echo 安装后任选其一：
echo   - 用「Inno Setup Compiler」打开 installer\KaomojiAssistant.iss 并点击 Build
echo   - 或把 ISCC 所在目录加入 PATH 后双击本脚本
echo   - 或手动运行： "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\KaomojiAssistant.iss
echo.
pause
exit /b 1

:done
echo.
echo 若成功，安装包位于 installer\out\KaomojiAssistant-Setup.exe
pause
