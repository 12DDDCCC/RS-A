@echo off
rem 开发辅助: 把全局 dsh 安装内的 @deepseek-ai/* 包以 junction 形式链入插件
rem node_modules, 供 tsc 类型解析使用。运行时不需要它们 (dsh loader 自行解析)。
rem 注意: 在本目录执行过 npm install 后 junction 可能被 npm 清理, 重跑本脚本即可。
setlocal
set P=%~dp0
set S=C:\Users\DELL\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai
if not exist "%P%node_modules" mkdir "%P%node_modules"
if not exist "%P%node_modules\@deepseek-ai" mkdir "%P%node_modules\@deepseek-ai"
if not exist "%P%node_modules\@types" mkdir "%P%node_modules\@types"
for /d %%D in ("%S%\*") do (
  if not exist "%P%node_modules\@deepseek-ai\%%~nxD" mklink /J "%P%node_modules\@deepseek-ai\%%~nxD" "%%D" >nul 2>&1
)
if not exist "%P%node_modules\@types\node" mklink /J "%P%node_modules\@types\node" "D:\AI\_workspace\frontend\node_modules\@types\node" >nul 2>&1
echo dev links ready
endlocal
