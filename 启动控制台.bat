@echo off
chcp 936 >nul
cd /d "%~dp0"

rem ============================================================
rem  mpv 实时超分 + 补帧 —— 图形控制台
rem
rem  用法：
rem    双击                     无控制台窗口启动（界面自己的窗口是唯一可见窗口）
rem    启动控制台.bat debug     保留控制台窗口，排错用（能直接看到 Python 报错）
rem
rem  ★ 更推荐双击同目录的「实时播放器.lnk」：它直接指向 pythonw.exe、
rem    连 bat 都不经过，双击连那几十毫秒的 cmd 闪烁都没有。
rem    （那个快捷方式由 make_shortcut.py 生成，删了重跑一次即可重建。）
rem
rem  ★ 为什么用 pythonw + start：
rem    python.exe 会在任务栏留一个黑窗。pythonw.exe 是 GUI 子系统、完全没有
rem    控制台；再用 start 让 bat 立刻退出，连 cmd 窗口都不留。
rem    代价是 pythonw 下未捕获异常会**静默消失** —— 所以 live_gui.py 里装了
rem    崩溃兜底：堆栈写进 console_error.log，并弹框告诉用户去哪看。
rem    （mpv.exe 本身也是 GUI 子系统、Subsystem=2，不会自己弹控制台，已确认。）
rem
rem  ★ 界面需要 PySide6 —— 2026-09-30 起改由**本目录 .venv** 提供
rem    （自带 Python 3.13 + PySide6，整个文件夹拷走就能跑）。
rem    策略：.venv 优先 → 找不到再回退本机 Python（有 PySide6 的那个）。
rem ============================================================

set "VENV=%~dp0.venv"
set "GUI=%~dp0live_gui.py"

if exist "%VENV%\python.exe" (
    set "PY=%VENV%\python.exe"
    set "PYW=%VENV%\pythonw.exe"
    goto :have_py
)

rem —— 回退：本机系统 Python（按可能性排序）——
set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
set "PYW=%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
if exist "%PY%" goto :have_py
set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"

:have_py

if not exist "%GUI%" (
    echo [X] 找不到 live_gui.py
    pause
    exit /b 1
)

if not exist "%PY%" (
    echo [X] 找不到可用的 Python 解释器（.venv 与系统 Python 都没有）
    echo     期望位置（自带）: %VENV%\python.exe
    echo     图形界面依赖 PySide6。想用命令行方式可以直接跑 live_play.bat。
    pause
    exit /b 1
)

rem 依赖预检：pythonw 下 import 失败是完全静默的，先用带控制台的解释器确认一次
"%PY%" -c "import PySide6" 2>nul
if errorlevel 1 (
    echo [X] 这个解释器里没有 PySide6，图形控制台起不来：
    echo     %PY%
    echo     装它："%PY%" -m pip install PySide6
    echo     或改用命令行方式：live_play.bat
    pause
    exit /b 1
)

if /i "%~1"=="debug" goto :debug

if not exist "%PYW%" (
    echo [!] 找不到 pythonw.exe，只能带控制台启动。
    goto :debug
)

rem 无窗口启动：bat 立刻退出，任务栏只留界面自己的窗口
start "" "%PYW%" "%GUI%"
exit /b 0

:debug
"%PY%" "%GUI%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo ------------------------------------------------
    echo 控制台异常退出，返回码 %RC%
    echo 上面的报错信息请留着，那是排查用的线索。
    echo ------------------------------------------------
    pause
)
exit /b %RC%
